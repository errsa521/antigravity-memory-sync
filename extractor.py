"""Incremental extractor with workspace/subagent filtering and local secret redaction."""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from config import LOCAL_TZ, Settings
from sanitizer import redact_sensitive

USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
STRIP_BLOCKS_RE = re.compile(
    r"<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>.*?</(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>",
    re.DOTALL,
)
AUTO_ERROR_PREFIXES = (
    'The MCP server "',
    "MCP server ",
)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _norm_path(p: str) -> str:
    return p.strip().replace("\\", "/").rstrip("/").lower()


def _parse_local_dt(iso_str: str) -> datetime | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None


def get_conversation_workspaces(conversations_dir: Path, conv_id: str) -> list[str]:
    """Read workspace paths bound to conv_id from conversations/<id>.db (read-only)."""
    db_path = conversations_dir / f"{conv_id}.db"
    if not db_path.is_file():
        return []
    found: set[str] = set()
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            for (blob,) in con.execute("SELECT data FROM trajectory_metadata_blob").fetchall():
                if not isinstance(blob, bytes):
                    continue
                idx = 0
                while True:
                    pos = blob.find(b"file:///", idx)
                    if pos == -1:
                        break
                    length = None
                    if pos >= 1 and 8 <= blob[pos - 1] < 128:
                        length = blob[pos - 1]
                    elif pos >= 2 and (blob[pos - 2] & 0x80):
                        length = (blob[pos - 2] & 0x7F) | ((blob[pos - 1] & 0x7F) << 7)
                    if length and pos + length <= len(blob):
                        raw = blob[pos : pos + length]
                        try:
                            s = raw.decode("utf-8")
                            if all(ord(c) >= 32 for c in s):
                                decoded = urllib.parse.unquote(s)
                                if decoded.startswith("file:///"):
                                    found.add(decoded[8:].replace("\\", "/").rstrip("/"))
                        except Exception:
                            pass
                    idx = pos + 8
        finally:
            con.close()
    except Exception:
        return []
    return sorted(found)


def _matches_workspace_filter(workspaces: list[str], filters: tuple[str, ...]) -> bool:
    if not filters:
        return True
    if not workspaces:
        return False
    norm_ws = [_norm_path(w) for w in workspaces]
    for f in filters:
        nf = _norm_path(f)
        if not nf:
            continue
        for w in norm_ws:
            if w == nf or w.startswith(nf + "/") or w.endswith("/" + nf):
                return True
    return False


def _is_subagent_or_system_noise(transcript_path: Path) -> bool:
    """Detect subagent internal runs or one-line auto-generated MCP startup error sessions."""
    first_rec: dict[str, Any] | None = None
    user_count = 0
    assistant_count = 0
    with transcript_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for idx, raw in enumerate(fh):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if first_rec is None:
                first_rec = rec
            t = rec.get("type")
            if t == "USER_INPUT":
                user_count += 1
            elif t == "PLANNER_RESPONSE" and str(rec.get("content", "")).strip():
                assistant_count += 1
            if idx >= 12 and user_count >= 1 and assistant_count >= 1:
                break

    if not first_rec:
        return True
    # Genuine top-level user chats start with USER_EXPLICIT + USER_INPUT + <USER_REQUEST>
    if first_rec.get("source") != "USER_EXPLICIT" or first_rec.get("type") != "USER_INPUT":
        return True
    first_content = str(first_rec.get("content", ""))
    if "<USER_REQUEST>" not in first_content:
        return True
    m = USER_REQ_RE.search(first_content)
    req_text = m.group(1).strip() if m else first_content.strip()
    if user_count <= 1 and assistant_count == 0 and any(req_text.startswith(p) for p in AUTO_ERROR_PREFIXES):
        return True
    return False


def _clean_text(msg_type: str, content: str, max_chars: int, redact: bool) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    if msg_type == "USER_INPUT":
        m = USER_REQ_RE.search(text)
        text = m.group(1).strip() if m else STRIP_BLOCKS_RE.sub("", text).strip()
    else:
        text = STRIP_BLOCKS_RE.sub("", text).strip()
    if redact:
        text = redact_sensitive(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…[截断]"
    return text


def extract_incremental(settings: Settings, dry_run: bool = False) -> dict[str, Any]:
    """Scan eligible transcript.jsonl files from saved line cursors and bucket by local date."""
    cursors: dict[str, dict[str, Any]] = _load_json(settings.cursors_path, {})
    buckets: dict[str, list[dict[str, Any]]] = {}
    stats: dict[str, Any] = {
        "files_scanned": 0,
        "skipped_workspace": 0,
        "skipped_subagent": 0,
        "new_messages": 0,
        "dates_touched": 0,
    }

    if not settings.brain_dir.exists():
        return stats

    pattern = "*/.system_generated/logs/transcript.jsonl"
    for transcript in sorted(settings.brain_dir.glob(pattern)):
        conv_id = transcript.parents[2].name
        if settings.skip_subagents and _is_subagent_or_system_noise(transcript):
            stats["skipped_subagent"] += 1
            continue

        workspaces = get_conversation_workspaces(settings.conversations_dir, conv_id)
        if not _matches_workspace_filter(workspaces, settings.workspace_filters):
            stats["skipped_workspace"] += 1
            continue

        cur = cursors.get(conv_id, {})
        last_line = int(cur.get("line", 0))
        mtime = transcript.stat().st_mtime
        size = transcript.stat().st_size

        if cur.get("size") == size and cur.get("mtime") == mtime:
            continue

        stats["files_scanned"] += 1
        line_no = 0
        new_in_file = 0
        ws_tag = Path(workspaces[0]).name if workspaces else ""
        with transcript.open("r", encoding="utf-8", errors="ignore") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                if line_no <= last_line:
                    continue
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    rec = json.loads(raw_line)
                except Exception:
                    continue
                rec_type = rec.get("type")
                if rec_type not in ("USER_INPUT", "PLANNER_RESPONSE"):
                    continue
                cleaned = _clean_text(
                    rec_type, rec.get("content", ""), settings.max_msg_chars, settings.redact_secrets
                )
                if not cleaned:
                    continue
                dt = _parse_local_dt(rec.get("created_at", ""))
                if dt is None:
                    dt = datetime.fromtimestamp(mtime, tz=LOCAL_TZ)
                date_key = dt.strftime("%Y-%m-%d")
                buckets.setdefault(date_key, []).append(
                    {
                        "ts": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "conv": conv_id[:8],
                        "ws": ws_tag,
                        "role": "user" if rec_type == "USER_INPUT" else "assistant",
                        "text": cleaned,
                    }
                )
                new_in_file += 1

        stats["new_messages"] += new_in_file
        cursors[conv_id] = {"line": line_no, "size": size, "mtime": mtime}

    stats["dates_touched"] = len(buckets)
    stats["by_date"] = {
        d: {"messages": len(items), "chars": sum(len(x["text"]) for x in items)}
        for d, items in sorted(buckets.items())
    }
    if dry_run:
        return stats

    settings.raw_daily_dir.mkdir(parents=True, exist_ok=True)
    for date_key, items in buckets.items():
        items.sort(key=lambda x: x["ts"])
        out_path = settings.raw_daily_dir / f"{date_key}.jsonl"
        with out_path.open("a", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    _save_json(settings.cursors_path, cursors)
    return stats


def _format_rows_to_lines(settings: Settings, rows: list[dict[str, Any]]) -> list[str]:
    rows = sorted(rows, key=lambda x: x.get("ts", ""))
    lines: list[str] = []
    for r in rows:
        hhmm = str(r.get("ts", ""))[11:16] or "??:??"
        role = settings.user_label if r.get("role") == "user" else settings.assistant_label
        conv = r.get("conv", "")
        ws = f" @{r['ws']}" if r.get("ws") else ""
        text = str(r.get("text", "")).replace("\n", " ")
        if settings.redact_secrets:
            text = redact_sensitive(text)
        lines.append(f"[{hhmm} #{conv}{ws}] {role}: {text}")
    return lines


def split_lines_into_chunks(lines: list[str], max_chars: int) -> list[str]:
    """Chronologically chunk dialogue lines so long days never drop the middle section."""
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for line in lines:
        add_len = len(line) + (1 if buf else 0)
        if buf and buf_len + add_len > max_chars:
            chunks.append("\n".join(buf))
            buf = [line]
            buf_len = len(line)
        else:
            buf.append(line)
            buf_len += add_len
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def read_day_chunks_since(settings: Settings, date_str: str, start_line: int = 0) -> tuple[list[str], int]:
    """Read raw jsonl lines after start_line and return (chronological_chunks, total_lines)."""
    path = settings.raw_daily_dir / f"{date_str}.jsonl"
    if not path.exists():
        return [], 0
    raw_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    total_lines = len(raw_lines)
    if start_line >= total_lines:
        return [], total_lines

    rows: list[dict[str, Any]] = []
    for raw in raw_lines[start_line:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except Exception:
            continue
    if not rows:
        return [], total_lines
    formatted = _format_rows_to_lines(settings, rows)
    return split_lines_into_chunks(formatted, settings.max_input_chars), total_lines
