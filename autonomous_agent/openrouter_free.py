from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

OPENROUTER_FREE_MODEL = "openrouter/free"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

@dataclass(frozen=True)
class OpenRouterResult:
    attempted: bool
    success: bool
    latency_ms: int | None
    text: str = ""
    detail: str = ""

def chat_free(prompt: str, *, timeout: float = 20.0) -> OpenRouterResult:
    """Call only OpenRouter's explicitly free router; never fall back to paid models."""
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return OpenRouterResult(False, False, None, detail="OPENROUTER_API_KEY is not configured.")
    started = time.perf_counter()
    try:
        response = httpx.post(
            OPENROUTER_CHAT_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": OPENROUTER_FREE_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return OpenRouterResult(True, False, int((time.perf_counter() - started) * 1000), detail=f"request failed: {exc}")
    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code == 429:
        return OpenRouterResult(True, False, latency_ms, detail="rate limited; no paid retry attempted")
    if response.status_code >= 400:
        return OpenRouterResult(True, False, latency_ms, detail=f"HTTP {response.status_code}; no paid fallback attempted")
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return OpenRouterResult(True, False, latency_ms, detail=f"invalid response: {exc}")
    if not isinstance(text, str) or not text.strip():
        return OpenRouterResult(True, False, latency_ms, detail="empty model response")
    return OpenRouterResult(True, True, latency_ms, text=text)
