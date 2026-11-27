"""Minimal OpenAI-compatible HTTP client using Python standard library."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from config import Settings


def _build_endpoint(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if not url:
        raise ValueError("API_BASE_URL is empty. Fill it in .env first.")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


def chat_complete(
    settings: Settings,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    temperature: float = 0.2,
    retries: int = 2,
) -> str:
    """Call OpenAI-compatible /v1/chat/completions endpoint and return assistant text."""
    if not settings.api_key or settings.api_key.startswith("sk-your-"):
        raise ValueError("API_KEY is missing or still placeholder. Fill it in .env first.")

    endpoint = _build_endpoint(settings.api_base_url)
    payload: dict[str, Any] = {
        "model": settings.api_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.api_key}",
        "User-Agent": "agy-memory-sync/1.0",
    }

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=settings.api_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError(f"Empty choices in API response: {raw[:300]}")
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            text = str(content or "").strip()
            if not text:
                raise RuntimeError(f"Empty message content in API response: {raw[:300]}")
            return text
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            last_err = RuntimeError(f"HTTP {e.code} from {endpoint}: {err_body}")
            if e.code in (400, 401, 403, 404):
                break
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"LLM API request failed after retries: {last_err}")
