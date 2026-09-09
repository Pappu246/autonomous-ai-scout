from __future__ import annotations

from typing import Iterable

from .models import AccessStatus, ModelCandidate


OFFICIAL_SOURCES = {
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
    "huggingface": "https://huggingface.co/docs/inference-providers/pricing",
}


def candidates_from_registry(items: Iterable[dict]) -> list[ModelCandidate]:
    """Turn configured providers into safe discovery candidates.

    A provider is never considered usable merely because it exists in this registry;
    verification must happen against its official source first.
    """
    candidates: list[ModelCandidate] = []
    for item in items:
        provider = str(item.get("id", "")).strip()
        if not provider:
            continue
        model = str(item.get("model", "discovery-pending")).strip() or "discovery-pending"
        source_url = item.get("docs") or OFFICIAL_SOURCES.get(provider)
        if not source_url:
            continue
        candidates.append(
            ModelCandidate(
                provider=provider,
                model=model,
                source_url=source_url,
                access_status=AccessStatus.UNKNOWN,
            )
        )
    return candidates
