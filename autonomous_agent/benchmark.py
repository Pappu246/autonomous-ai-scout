from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class BenchmarkResult:
    provider: str
    model: str
    attempted: bool
    success: bool
    latency_ms: int | None
    note: str


def benchmark_gemini(model: str) -> BenchmarkResult:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return BenchmarkResult("gemini", model, False, False, None, "GEMINI_API_KEY not configured; benchmark skipped to avoid paid/unknown access.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {"contents": [{"parts": [{"text": "Return exactly: SCOUT_OK"}]}]}
    started = time.perf_counter()
    try:
        r = httpx.post(url, params={"key": key}, json=payload, timeout=20)
        elapsed = int((time.perf_counter() - started) * 1000)
        if r.status_code == 429:
            return BenchmarkResult("gemini", model, True, False, elapsed, "Rate limited; no paid retry attempted.")
        r.raise_for_status()
        text = r.text.lower()
        return BenchmarkResult("gemini", model, True, "scout_ok" in text, elapsed, "Free-tier benchmark request completed." if "scout_ok" in text else "Request completed but expected marker was not observed.")
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return BenchmarkResult("gemini", model, True, False, elapsed, f"Benchmark failed without retry: {type(exc).__name__}.")
