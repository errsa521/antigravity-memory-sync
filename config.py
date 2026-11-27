"""Configuration loader for Antigravity -> Basic Memory sync."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOCAL_TZ = timezone(timedelta(hours=8))


def load_dotenv(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


@dataclass(frozen=True)
class Settings:
    brain_dir: Path
    memory_dir: Path
    state_dir: Path
    api_base_url: str
    api_key: str
    api_model: str
    api_timeout: int
    retention_days: int
    week_max_chars: int
    longterm_max_tokens: int
    daily_max_tokens: int
    today_max_tokens: int
    max_msg_chars: int
    max_input_chars: int
    user_label: str
    assistant_label: str
    profile_note_link: str
    hot_memory_enabled: bool
    hot_memory_path: Path
    hot_memory_max_chars: int
    hot_week_days: int

    @property
    def today_md(self) -> Path:
        return self.memory_dir / "daily" / "agy-today.md"

    @property
    def week_md(self) -> Path:
        return self.memory_dir / "daily" / "agy-week.md"

    @property
    def longterm_md(self) -> Path:
        return self.memory_dir / "profile" / "agy-longterm.md"

    @property
    def raw_daily_dir(self) -> Path:
        return self.state_dir / "raw-daily"

    @property
    def daily_archive_dir(self) -> Path:
        return self.state_dir / "daily-archive"

    @property
    def folded_archive_dir(self) -> Path:
        return self.state_dir / "folded-archive"

    @property
    def cursors_path(self) -> Path:
        return self.state_dir / "cursors.json"

    @property
    def today_state_path(self) -> Path:
        return self.state_dir / "today-state.json"

    @property
    def daily_state_path(self) -> Path:
        return self.state_dir / "daily-state.json"

    @property
    def longterm_fp_path(self) -> Path:
        return self.state_dir / "longterm.fingerprint"


def get_settings() -> Settings:
    env_file = load_dotenv(BASE_DIR / ".env")

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key) or env_file.get(key) or default

    home = Path.home()
    brain = Path(get("AGY_BRAIN_DIR", str(home / ".gemini" / "antigravity" / "brain")))
    memory = Path(get("BASIC_MEMORY_DIR", str(home / "basic-memory")))
    state = Path(get("AGY_SYNC_STATE_DIR", str(BASE_DIR / "state")))

    return Settings(
        brain_dir=brain,
        memory_dir=memory,
        state_dir=state,
        api_base_url=get("API_BASE_URL", "").rstrip("/"),
        api_key=get("API_KEY", ""),
        api_model=get("API_MODEL", "gemini-2.5-flash"),
        api_timeout=int(get("API_TIMEOUT", "90")),
        retention_days=int(get("RETENTION_DAYS", "6")),
        week_max_chars=int(get("WEEK_MAX_CHARS", "2500")),
        longterm_max_tokens=int(get("LONGTERM_MAX_TOKENS", "1250")),
        daily_max_tokens=int(get("DAILY_MAX_TOKENS", "500")),
        today_max_tokens=int(get("TODAY_MAX_TOKENS", "600")),
        max_msg_chars=int(get("MAX_MSG_CHARS", "800")),
        max_input_chars=int(get("MAX_INPUT_CHARS", "16000")),
        user_label=get("USER_LABEL", "User"),
        assistant_label=get("ASSISTANT_LABEL", "Assistant"),
        profile_note_link=get("PROFILE_NOTE_LINK", ""),
        hot_memory_enabled=get("HOT_MEMORY_ENABLED", "true").lower() not in ("0", "false", "no", "off"),
        hot_memory_path=Path(
            get("HOT_MEMORY_PATH", str(home / ".gemini" / "config" / "rules" / "agy-hot-memory.md"))
        ),
        hot_memory_max_chars=int(get("HOT_MEMORY_MAX_CHARS", "1500")),
        hot_week_days=int(get("HOT_WEEK_DAYS", "3")),
    )
