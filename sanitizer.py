"""Local regex sanitizer to redact secrets & PII before saving raw logs or calling external APIs."""
from __future__ import annotations

import re

# Ordered from most specific tokens to generic patterns
_REDACT_RULES: list[tuple[re.Pattern[str], str]] = [
    # Common API keys & personal access tokens
    (re.compile(r"\b(?:sk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{16,})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\b(?:AIza[0-9A-Za-z_\-]{25,})\b"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\b(?:AKIA[0-9A-Z]{16})\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9\-]{10,})\b"), "[REDACTED_SLACK_TOKEN]"),
    # Authorization headers and key=value assignments
    (re.compile(r"(?i)(Authorization\s*[:=]\s*Bearer\s+)[A-Za-z0-9._\-]{12,}"), r"\1[REDACTED_TOKEN]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key|password|passwd|pwd)\s*[:=]\s*(['\"]?)[^\s'\",;]{6,}\2"),
        r"\1=\2[REDACTED]\2",
    ),
    # Private key blocks
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # Mainland China 11-digit mobile numbers (strict non-digit boundaries)
    (re.compile(r"(?<!\d)(?:\+?86[\-\s]?)?(1[3-9]\d{9})(?!\d)"), "[REDACTED_PHONE]"),
]

_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def _redact_email(match: re.Match[str]) -> str:
    addr = match.group(0)
    if addr.lower().endswith("@users.noreply.github.com"):
        return addr
    return "[REDACTED_EMAIL]"


def redact_sensitive(text: str) -> str:
    """Replace detected API keys, tokens, credentials, emails, and phone numbers locally."""
    if not text:
        return ""
    out = text
    for pattern, repl in _REDACT_RULES:
        out = pattern.sub(repl, out)
    out = _EMAIL_RE.sub(_redact_email, out)
    return out
