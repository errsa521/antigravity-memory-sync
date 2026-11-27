"""Hot-memory rule assembler with Antigravity always_on frontmatter and reserved budgets."""
from __future__ import annotations

from pathlib import Path

from config import Settings


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


def _strip_footer_links(text: str) -> str:
    idx = text.find("\n## 关联")
    return text[:idx].strip() if idx != -1 else text.strip()


def _truncate_clean(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit <= 12:
        return text[:limit]
    return text[: limit - 6].rstrip() + "\n…(略)"


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def assemble_hot_memory(
    settings: Settings,
    today_str: str,
    recent_day_paths: list[tuple[str, Path]],
    dry_run: bool = False,
) -> str:
    """Assemble ~/.gemini/config/rules/agy-hot-memory.md with guaranteed long-term budget."""
    if not settings.hot_memory_enabled:
        return "disabled"

    lt_text = ""
    if settings.longterm_md.exists():
        lt_text = _strip_footer_links(
            _strip_frontmatter(settings.longterm_md.read_text(encoding="utf-8", errors="ignore"))
        )

    today_text = ""
    if settings.today_md.exists():
        raw_t = _strip_footer_links(
            _strip_frontmatter(settings.today_md.read_text(encoding="utf-8", errors="ignore"))
        )
        if raw_t and "暂无新增记录" not in raw_t:
            today_text = raw_t

    week_blocks = [p.read_text(encoding="utf-8", errors="ignore").strip() for _, p in reversed(recent_day_paths)]
    week_blocks = [b for b in week_blocks if b]
    week_text = "\n\n".join(week_blocks)

    if not any((lt_text, today_text, week_text)):
        return "empty"

    frontmatter = (
        "---\n"
        "trigger: always_on\n"
        'description: "Auto-generated Antigravity hot memory (long-term core, today, and recent days)"\n'
        "---\n\n"
        "# Antigravity 热记忆预热（自动生成）\n\n"
        f"> 同步日期：{today_str} | 由 `agy-memory-sync` 自动维护；完整历史与细节请检索 `basic-memory` MCP。\n"
    )

    total_budget = max(600, settings.hot_memory_max_chars)
    body_budget = max(300, total_budget - len(frontmatter) - 60)

    # Reserve guaranteed budgets so long-term core is never truncated away by today/week chatter
    lt_alloc = min(len(lt_text), settings.hot_longterm_reserve) if lt_text else 0
    today_alloc = min(len(today_text), settings.hot_today_reserve) if today_text else 0
    remaining = max(0, body_budget - lt_alloc - today_alloc)

    week_alloc = min(len(week_text), remaining) if week_text else 0
    remaining -= week_alloc

    # Redistribute any unused budget back to sections that still have content
    if remaining > 0 and today_text and len(today_text) > today_alloc:
        extra = min(remaining, len(today_text) - today_alloc)
        today_alloc += extra
        remaining -= extra
    if remaining > 0 and lt_text and len(lt_text) > lt_alloc:
        extra = min(remaining, len(lt_text) - lt_alloc)
        lt_alloc += extra
        remaining -= extra

    parts = [frontmatter.rstrip()]
    if lt_text and lt_alloc >= 40:
        parts.append("## 长期沉淀核心\n\n" + _truncate_clean(lt_text, lt_alloc))
    if today_text and today_alloc >= 40:
        parts.append("## 今日高价值进展\n\n" + _truncate_clean(today_text, today_alloc))
    if week_text and week_alloc >= 40:
        parts.append(f"## 最近 {len(week_blocks)} 天重点\n\n" + _truncate_clean(week_text, week_alloc))

    final_text = "\n\n".join(parts).rstrip() + "\n"
    if dry_run:
        return f"would_write ({len(final_text)} chars)"

    if settings.hot_memory_path.exists():
        old_text = settings.hot_memory_path.read_text(encoding="utf-8", errors="ignore")
        if old_text == final_text:
            return f"unchanged ({len(final_text)} chars)"

    _write_atomic(settings.hot_memory_path, final_text)
    return f"written ({len(final_text)} chars)"
