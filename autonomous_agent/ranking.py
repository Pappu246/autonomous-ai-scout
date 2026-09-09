from __future__ import annotations

from .models import ModelCandidate


def score_candidate(candidate: ModelCandidate, task: str) -> float:
    """Simple deterministic router score; safe by default.

    Only verified-free candidates receive a positive score. Task matching can be
    expanded later with benchmarks and historical success metrics.
    """
    if candidate.access_status.value != "verified_free":
        return 0.0
    score = 50.0
    task_l = task.lower()
    model_l = candidate.model.lower()
    if "code" in task_l and any(x in model_l for x in ("code", "coder")):
        score += 25
    if "fast" in task_l:
        score += 10
    return min(score, 100.0)


def choose_model(candidates: list[ModelCandidate], task: str) -> ModelCandidate | None:
    usable = [(score_candidate(c, task), c) for c in candidates]
    usable = [(s, c) for s, c in usable if s > 0]
    if not usable:
        return None
    usable.sort(key=lambda pair: pair[0], reverse=True)
    return usable[0][1]
