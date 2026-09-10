from __future__ import annotations

import hashlib
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


def _headline(text: str) -> str:
    """Extract a short deterministic release marker without interpreting arbitrary prose."""
    for line in text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip(" #*-\t")
        if not clean:
            continue
        if re.match(r"^(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2})$", clean, re.I):
            return clean
        if re.search(r"\b(?:release|released|changelog|version|launch|launched|deprecat|shutdown)\b", clean, re.I):
            return clean[:240]
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
