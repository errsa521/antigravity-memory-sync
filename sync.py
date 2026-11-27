"""CLI entrypoint for Antigravity -> Basic Memory auto-sync."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from compiler import run_pipeline
from config import LOCAL_TZ, get_settings
from extractor import extract_incremental
from llm_client import chat_complete


def _append_log(log_path: Path, line: str, max_lines: int = 500) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Hana-style rolling memory sync for Antigravity.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without calling API or writing notes.")
    parser.add_argument("--force-today", action="store_true", help="Force re-compile today's note even if unchanged.")
    parser.add_argument("--test-api", action="store_true", help="Send a tiny ping to verify .env API settings.")
    args = parser.parse_args()

    settings = get_settings()
    log_path = settings.state_dir / "sync.log"
    ts = datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")

    if args.test_api:
        try:
            reply = chat_complete(
                settings,
                "You are a concise health-check responder.",
                "Reply with exactly: OK",
                max_tokens=16,
                temperature=0.0,
                retries=0,
            )
            print(f"[OK] API connected ({settings.api_model}): {reply}")
            return 0
        except Exception as e:
            print(f"[FAIL] API test failed: {e}", file=sys.stderr)
            return 1

    try:
        ext_stats = extract_incremental(settings, dry_run=args.dry_run)
        pipe_report = run_pipeline(settings, dry_run=args.dry_run, force_today=args.force_today)
        result = {"extract": ext_stats, "compile": pipe_report}
        if not args.dry_run:
            _append_log(log_path, f"[{ts}] OK {json.dumps(result, ensure_ascii=False)}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        if not args.dry_run:
            _append_log(log_path, f"[{ts}] ERROR {e}")
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
