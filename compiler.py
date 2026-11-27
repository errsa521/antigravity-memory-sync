"""Hana-style rolling memory compiler for Basic Memory (agy-* notes)."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config import LOCAL_TZ, Settings
from extractor import read_day_transcript_text
from llm_client import chat_complete
from prompts import PROMPT_DAILY, PROMPT_LONGTERM, PROMPT_TODAY

DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
RAW_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")


def _now_local() -> datetime:
    return datetime.now(tz=LOCAL_TZ)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _strip_frontmatter(md_text: str) -> str:
    text = md_text.strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2].strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _wrap_bm_note(title: str, permalink: str, tags: list[str], body: str, footer: str = "") -> str:
    tag_lines = "\n".join(f"- {t}" for t in tags)
    header = (
        f"---\n"
        f"title: {title}\n"
        f"type: note\n"
        f"tags:\n{tag_lines}\n"
        f"permalink: {permalink}\n"
        f"---\n\n"
        f"# {title}\n\n"
    )
    parts = [header + body.strip()]
    if footer.strip():
        parts.append(footer.strip())
    return "\n\n".join(parts).rstrip() + "\n"


def _list_archive_days(archive_dir: Path) -> list[tuple[str, Path]]:
    if not archive_dir.exists():
        return []
    out: list[tuple[str, Path]] = []
    for p in sorted(archive_dir.iterdir()):
        m = DATE_FILE_RE.match(p.name)
        if p.is_file() and m:
            out.append((m.group(1), p))
    return out


def compile_past_days(settings: Settings, today_str: str, dry_run: bool = False) -> list[str]:
    """Compile any raw-daily/<date>.jsonl with date < today into state/daily-archive/<date>.md."""
    if not settings.raw_daily_dir.exists():
        return []
    compiled: list[str] = []
    settings.daily_archive_dir.mkdir(parents=True, exist_ok=True)
    settings.folded_archive_dir.mkdir(parents=True, exist_ok=True)

    for p in sorted(settings.raw_daily_dir.iterdir()):
        m = RAW_FILE_RE.match(p.name)
        if not p.is_file() or not m:
            continue
        date_str = m.group(1)
        if date_str >= today_str:
            continue
        archive_path = settings.daily_archive_dir / f"{date_str}.md"
        folded_path = settings.folded_archive_dir / f"{date_str}.md"
        if archive_path.exists() or folded_path.exists():
            continue
        raw_text = read_day_transcript_text(settings, date_str)
        if not raw_text:
            continue
        if dry_run:
            compiled.append(f"{date_str} ({len(raw_text)} chars)")
            continue
        user_msg = f"日期：{date_str}\n\n## 当日对话记录\n{raw_text}"
        summary = chat_complete(
            settings, PROMPT_DAILY, user_msg, max_tokens=settings.daily_max_tokens
        )
        _write_atomic(archive_path, f"## {date_str}\n\n{summary.strip()}\n")
        compiled.append(date_str)
    return compiled


def compile_today(
    settings: Settings, today_str: str, dry_run: bool = False, force: bool = False
) -> str:
    """Incrementally compile today's conversations into basic-memory/daily/agy-today.md."""
    raw_text = read_day_transcript_text(settings, today_str)
    state = _load_json(settings.today_state_path, {})

    if not raw_text:
        if state.get("logicalDate") != today_str and not dry_run:
            body = f"> 更新日期：{today_str}\n\n暂无新增记录。"
            _write_atomic(
                settings.today_md,
                _wrap_bm_note("Antigravity 今日动态", "main/daily/agy-today", ["daily", "antigravity"], body),
            )
            _save_json(
                settings.today_state_path,
                {"logicalDate": today_str, "fingerprint": "", "updatedAt": _now_local().isoformat()},
            )
        return "empty"

    fp = _sha256(raw_text)
    if not force and state.get("logicalDate") == today_str and state.get("fingerprint") == fp:
        return "unchanged"
    if dry_run:
        return f"would_compile ({len(raw_text)} chars)"

    existing = ""
    if state.get("logicalDate") == today_str and settings.today_md.exists():
        existing = _strip_frontmatter(settings.today_md.read_text(encoding="utf-8", errors="ignore"))

    parts = [f"日期：{today_str}"]
    if existing and "暂无新增记录" not in existing:
        parts.append(f"## 现有今日动态\n{existing}")
    parts.append(f"## 今日对话记录\n{raw_text}")

    summary = chat_complete(
        settings, PROMPT_TODAY, "\n\n".join(parts), max_tokens=settings.today_max_tokens
    )
    body = f"> 日期：{today_str} | 更新：{_now_local().strftime('%H:%M')}\n\n{summary.strip()}"
    footer_lines = ["## 关联", "- part_of [[agy-week]]"]
    if settings.profile_note_link:
        footer_lines.append(f"- about [[{settings.profile_note_link}]]")
    footer = "\n".join(footer_lines)
    _write_atomic(
        settings.today_md,
        _wrap_bm_note("Antigravity 今日动态", "main/daily/agy-today", ["daily", "antigravity"], body, footer),
    )
    _save_json(
        settings.today_state_path,
        {"logicalDate": today_str, "fingerprint": fp, "updatedAt": _now_local().isoformat()},
    )
    return "compiled"


def roll_daily_window(settings: Settings, today_str: str, dry_run: bool = False) -> list[str]:
    """Fold daily archives older than retention_days into profile/agy-longterm.md (Hana rollDailyWindow)."""
    cutoff = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=settings.retention_days)).strftime(
        "%Y-%m-%d"
    )
    archives = _list_archive_days(settings.daily_archive_dir)
    expired = [(d, p) for d, p in archives if d < cutoff]
    if not expired:
        return []

    chunks = [p.read_text(encoding="utf-8", errors="ignore").strip() for _, p in expired]
    newly_settled = "\n\n".join(c for c in chunks if c)
    if dry_run:
        return [f"{d} (expired)" for d, _ in expired]

    if newly_settled:
        fp = _sha256(newly_settled)
        old_fp = settings.longterm_fp_path.read_text(encoding="utf-8").strip() if settings.longterm_fp_path.exists() else ""
        if fp != old_fp or not settings.longterm_md.exists():
            prev_longterm = ""
            if settings.longterm_md.exists():
                prev_longterm = _strip_frontmatter(settings.longterm_md.read_text(encoding="utf-8", errors="ignore"))
            user_parts = []
            if prev_longterm:
                user_parts.append(f"## 上一份长期情况\n\n{prev_longterm}")
            user_parts.append(f"## 新沉淀内容\n\n{newly_settled}")
            compiled = chat_complete(
                settings,
                PROMPT_LONGTERM,
                "\n\n".join(user_parts),
                max_tokens=settings.longterm_max_tokens,
            )
            footer_lines = ["## 关联", "- has_recent_log [[agy-week]]"]
            if settings.profile_note_link:
                footer_lines.insert(1, f"- supplements [[{settings.profile_note_link}]]")
            footer = "\n".join(footer_lines)
            _write_atomic(
                settings.longterm_md,
                _wrap_bm_note(
                    "Antigravity 长期沉淀记忆",
                    "main/profile/agy-longterm",
                    ["profile", "antigravity", "longterm"],
                    compiled,
                    footer,
                ),
            )
            _write_atomic(settings.longterm_fp_path, fp + "\n")

    settings.folded_archive_dir.mkdir(parents=True, exist_ok=True)
    for d, p in expired:
        p.replace(settings.folded_archive_dir / p.name)
    return [d for d, _ in expired]


def assemble_week(settings: Settings, dry_run: bool = False) -> int:
    """Assemble recent N days (capped at week_max_chars) into basic-memory/daily/agy-week.md."""
    archives = _list_archive_days(settings.daily_archive_dir)[-settings.retention_days :]
    blocks = [p.read_text(encoding="utf-8", errors="ignore").strip() for _, p in archives]
    blocks = [b for b in blocks if b]
    if not blocks or dry_run:
        return len(blocks)

    assembled = "\n\n".join(blocks)
    while len(blocks) > 1 and len(assembled) > settings.week_max_chars:
        blocks.pop(0)
        assembled = "\n\n".join(blocks)
    if len(assembled) > settings.week_max_chars:
        assembled = assembled[: settings.week_max_chars]

    body = f"> 滚动窗口：最近 {settings.retention_days} 天（硬上限 {settings.week_max_chars} 字）\n\n{assembled}"
    footer_lines = ["## 关联", "- rolls_into [[agy-longterm]]"]
    if settings.profile_note_link:
        footer_lines.insert(1, f"- about [[{settings.profile_note_link}]]")
    footer = "\n".join(footer_lines)
    _write_atomic(
        settings.week_md,
        _wrap_bm_note("Antigravity 近一周动态", "main/daily/agy-week", ["daily", "antigravity"], body, footer),
    )
    return len(blocks)


def run_pipeline(settings: Settings, dry_run: bool = False, force_today: bool = False) -> dict[str, Any]:
    """Run daily state machine (catch-up on missed days + incremental today update)."""
    today_str = _now_local().strftime("%Y-%m-%d")
    daily_state = _load_json(settings.daily_state_path, {})
    same_day = daily_state.get("logicalDate") == today_str
    completed = dict(daily_state.get("completedSteps") or {}) if same_day else {}
    report: dict[str, Any] = {"date": today_str, "dry_run": dry_run}

    if "compileDaily" not in completed or dry_run:
        days = compile_past_days(settings, today_str, dry_run=dry_run)
        report["compileDaily"] = days
        if not dry_run:
            completed["compileDaily"] = _now_local().isoformat()
    else:
        report["compileDaily"] = "skipped(done)"

    if "rollDailyWindow" not in completed or dry_run:
        folded = roll_daily_window(settings, today_str, dry_run=dry_run)
        report["rollDailyWindow"] = folded
        if not dry_run:
            completed["rollDailyWindow"] = _now_local().isoformat()
    else:
        report["rollDailyWindow"] = "skipped(done)"

    report["assembleWeek"] = assemble_week(settings, dry_run=dry_run)
    report["compileToday"] = compile_today(settings, today_str, dry_run=dry_run, force=force_today)

    if not dry_run:
        _save_json(
            settings.daily_state_path,
            {
                "schemaVersion": 1,
                "logicalDate": today_str,
                "completedSteps": completed,
                "updatedAt": _now_local().isoformat(),
            },
        )
    return report
