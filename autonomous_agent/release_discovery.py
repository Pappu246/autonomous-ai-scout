from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from typing import Iterable

from .sources import fetch_source


@dataclass(frozen=True)
class ReleaseFinding:
    provider: str
    source_url: str
    digest: str
    changed: bool
    headline: str


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _visible_lines(text: str) -> list[str]:
    """Convert common HTML release pages into bounded, readable text lines."""
    cleaned = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", text)
    cleaned = re.sub(r"<[^>]+>", "\n", cleaned)
    cleaned = html.unescape(cleaned)
    lines: list[str] = []
    for line in cleaned.splitlines():
        clean = re.sub(r"\s+", " ", line).strip(" #*-\t\r")
        if clean:
            lines.append(clean)
    return lines


def _headline(text: str) -> str:
    """Extract a short deterministic release marker without leaking raw page markup."""
    lines = _visible_lines(text)
    visible_text = " ".join(lines)
    date_match = re.search(
        r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2})\b",
        visible_text,
        re.I,
    )
    date = date_match.group(0) if date_match else ""
    for clean in lines:
        if re.search(r"\b(?:release|released|changelog|version|launch|launched|deprecat|shutdown)\b", clean, re.I):
            if date and date not in clean:
                return f"{clean[:200]} — {date}"
            return clean[:240]
    if date:
        return date
    return "Official release source changed."


def discover_releases(items: Iterable[dict], previous_hashes: dict[str, str]) -> list[ReleaseFinding]:
    findings: list[ReleaseFinding] = []
    for item in items:
        if not item.get("enabled", False):
            continue
        provider = str(item.get("id", "")).strip()
        for source_url in item.get("release_sources", []) or []:
            url = str(source_url).strip()
            if not provider or not url:
                continue
            check = fetch_source(url)
            if not check.reachable:
                continue
            digest = _digest(check.text)
            previous = previous_hashes.get(url, "")
            findings.append(ReleaseFinding(provider, url, digest, bool(previous and previous != digest), _headline(check.text)))
    return findings
