"""Incremental extractor for Antigravity transcript.jsonl files."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config import LOCAL_TZ, Settings

USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
STRIP_BLOCKS_RE = re.compile(
    r"<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>.*?</(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>",
    re.DOTALL,
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


def _parse_local_dt(iso_str: str) -> datetime | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None


def _clean_text(msg_type: str, content: str, max_chars: int) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    if msg_type == "USER_INPUT":
        m = USER_REQ_RE.search(text)
        if m:
            text = m.group(1).strip()
        else:
            text = STRIP_BLOCKS_RE.sub("", text).strip()
    else:
        text = STRIP_BLOCKS_RE.sub("", text).strip()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…[截断]"
    return text


def extract_incremental(settings: Settings, dry_run: bool = False) -> dict[str, int]:
    """Scan all transcript.jsonl files from saved line cursors and bucket by local date."""
    cursors: dict[str, dict[str, Any]] = _load_json(settings.cursors_path, {})
    buckets: dict[str, list[dict[str, Any]]] = {}
    stats = {"files_scanned": 0, "new_messages": 0, "dates_touched": 0}

    if not settings.brain_dir.exists():
        return stats

    pattern = "*/.system_generated/logs/transcript.jsonl"
    for transcript in sorted(settings.brain_dir.glob(pattern)):
        conv_id = transcript.parents[2].name
        cur = cursors.get(conv_id, {})
        last_line = int(cur.get("line", 0))
        mtime = transcript.stat().st_mtime
        size = transcript.stat().st_size

        if cur.get("size") == size and cur.get("mtime") == mtime:
            continue

        stats["files_scanned"] += 1
        line_no = 0
        new_in_file = 0
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
                cleaned = _clean_text(rec_type, rec.get("content", ""), settings.max_msg_chars)
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


def read_day_transcript_text(settings: Settings, date_str: str) -> str:
    """Format a day's raw jsonl records into compact plain text for LLM compilation."""
    path = settings.raw_daily_dir / f"{date_str}.jsonl"
    if not path.exists():
        return ""
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except Exception:
            continue
    if not rows:
        return ""
    rows.sort(key=lambda x: x.get("ts", ""))

    lines: list[str] = []
    for r in rows:
        hhmm = str(r.get("ts", ""))[11:16] or "??:??"
        role = settings.user_label if r.get("role") == "user" else settings.assistant_label
        conv = r.get("conv", "")
        text = str(r.get("text", "")).replace("\n", " ")
        lines.append(f"[{hhmm} #{conv}] {role}: {text}")

    joined = "\n".join(lines)
    if len(joined) > settings.max_input_chars:
        head = joined[: settings.max_input_chars // 3]
        tail = joined[-(settings.max_input_chars * 2 // 3) :]
        joined = head + "\n...[中间记录已略]...\n" + tail
    return joined
