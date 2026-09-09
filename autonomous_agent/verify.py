from __future__ import annotations

from .models import AccessStatus, ModelCandidate
from .sources import fetch_source, source_has_free_signal


def verify_candidate(candidate: ModelCandidate) -> ModelCandidate:
    check = fetch_source(str(candidate.source_url))
    if not check.reachable:
        return candidate.model_copy(update={"access_status": AccessStatus.UNKNOWN, "evidence": "Official source was not reachable during this run."})
    if source_has_free_signal(check):
        return candidate.model_copy(
            update={
                "access_status": AccessStatus.VERIFIED_FREE,
                "evidence": f"Official source reachable: {check.title or candidate.source_url}",
            }
        )
    return candidate.model_copy(update={"access_status": AccessStatus.PAID_ONLY, "evidence": "No explicit free-access signal found on the official source."})


def free_candidates(candidates: list[ModelCandidate]) -> list[ModelCandidate]:
    return [c for c in candidates if c.access_status == AccessStatus.VERIFIED_FREE]
