from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class SourceCheck:
    url: str
    reachable: bool
    title: str = ""
    text: str = ""
    digest: str = ""


_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=8.0)


def fetch_source(url: str) -> SourceCheck:
    try:
        response = httpx.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": "autonomous-ai-scout/0.2"})
        response.raise_for_status()
        text = response.text[:300_000]
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        return SourceCheck(url=url, reachable=True, title=title, text=text, digest=digest)
    except (httpx.HTTPError, UnicodeError):
        return SourceCheck(url=url, reachable=False)


def source_has_free_signal(check: SourceCheck) -> bool:
    if not check.reachable:
        return False
    lowered = re.sub(r"\s+", " ", check.text.lower())
    positive = ("free tier", "free usage", "free credits", "free of charge", "free plan")
    negative = ("paid only", "no free tier", "payment required")
    return any(term in lowered for term in positive) and not any(term in lowered for term in negative)
