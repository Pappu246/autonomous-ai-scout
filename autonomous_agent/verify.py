from __future__ import annotations

from .models import AccessStatus, ModelCandidate
from .sources import fetch_source, source_has_free_signal


def verify_candidate(candidate: ModelCandidate) -> ModelCandidate:
    check = fetch_source(str(candidate.source_url))
    if not check.reachable:
        return candidate.model_copy(update={"access_status": AccessStatus.UNKNOWN, "evidence": "Official source was not reachable during this run."})
    if source_has_free_signal(check):
        return candidate.model_copy(update={"access_status": AccessStatus.VERIFIED_FREE, "evidence": check.title or "Official source explicitly exposes a free-access signal.", "source_hash": check.digest})
    return candidate.model_copy(update={"access_status": AccessStatus.PAID_ONLY, "evidence": "Official source did not expose a qualifying free-access signal."})


def free_candidates(candidates: list[ModelCandidate]) -> list[ModelCandidate]:
    return [c for c in candidates if c.access_status == AccessStatus.VERIFIED_FREE]
