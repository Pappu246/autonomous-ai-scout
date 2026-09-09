from __future__ import annotations

import hashlib
from typing import Iterable

from .models import AccessStatus, ModelCandidate
from .sources import fetch_source

OFFICIAL_SOURCES = {
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
    "groq": "https://console.groq.com/docs/rate-limits",
    "huggingface": "https://huggingface.co/docs/inference-providers/pricing",
}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def candidates_from_registry(items: Iterable[dict]) -> list[ModelCandidate]:
    candidates: list[ModelCandidate] = []
    for item in items:
        if not item.get("enabled", False):
            continue
        provider = str(item.get("id", "")).strip()
        source_url = item.get("docs") or OFFICIAL_SOURCES.get(provider)
        if not provider or not source_url:
            continue
        configured = item.get("models", []) or [item.get("model", "discovery-pending")]
        for model in configured:
            model = str(model).strip()
            if model:
                candidates.append(ModelCandidate(provider=provider, model=model, source_url=source_url, access_status=AccessStatus.UNKNOWN))
    return candidates


def discover_official_changes(candidates: list[ModelCandidate], previous_sources: dict[str, str]) -> list[ModelCandidate]:
    result: list[ModelCandidate] = []
    for candidate in candidates:
        check = fetch_source(str(candidate.source_url))
        if not check.reachable:
            result.append(candidate)
            continue
        digest = _hash(check.text)
        changed = bool(previous_sources.get(str(candidate.source_url))) and previous_sources.get(str(candidate.source_url)) != digest
        result.append(candidate.model_copy(update={"source_hash": digest, "source_changed": changed, "evidence": check.title or "Official provider source reachable."}))
    return result
