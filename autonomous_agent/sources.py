from __future__ import annotations

import re
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class SourceCheck:
    url: str
    reachable: bool
    title: str = ""
    text: str = ""


_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def fetch_source(url: str) -> SourceCheck:
    try:
        response = httpx.get(
            url,
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "autonomous-ai-scout/0.1"},
        )
        response.raise_for_status()
        text = response.text
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        return SourceCheck(url=url, reachable=True, title=title, text=text[:200_000])
    except (httpx.HTTPError, UnicodeError):
        return SourceCheck(url=url, reachable=False)


def source_has_free_signal(check: SourceCheck) -> bool:
    if not check.reachable:
        return False
    lowered = check.text.lower()
    return any(term in lowered for term in ("free tier", "free usage", "free credits", "pricing"))
