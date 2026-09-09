from __future__ import annotations

from dataclasses import dataclass

from .models import AccessStatus, ModelCandidate


@dataclass(frozen=True)
class RouteDecision:
    model: ModelCandidate | None
    score: float
    reasons: tuple[str, ...]


def _score(candidate: ModelCandidate, task: str) -> tuple[float, list[str]]:
    if candidate.access_status is not AccessStatus.VERIFIED_FREE:
        return -1.0, ["not verified free"]

    text = task.lower()
    score = 50.0
    reasons = ["verified free"]

    if candidate.benchmark_ok is True:
        score += 20
        reasons.append("benchmark passed")
    elif candidate.benchmark_ok is False:
        score -= 25
        reasons.append("benchmark failed")

    if candidate.benchmark_latency_ms is not None:
        if candidate.benchmark_latency_ms < 1000:
            score += 15
            reasons.append("low measured latency")
        elif candidate.benchmark_latency_ms > 5000:
            score -= 10
            reasons.append("high measured latency")

    model_text = f"{candidate.provider} {candidate.model}".lower()
    if any(word in text for word in ("code", "coding", "program", "debug")) and any(word in model_text for word in ("code", "coder", "qwen", "gpt-oss")):
        score += 10
        reasons.append("task/model fit: coding")
    if any(word in text for word in ("fast", "quick", "latency")) and candidate.benchmark_latency_ms is not None:
        score += max(0.0, 10.0 - candidate.benchmark_latency_ms / 1000.0)
        reasons.append("task fit: speed")

    return max(0.0, min(100.0, score)), reasons


def choose_model(candidates: list[ModelCandidate], task: str) -> RouteDecision:
    """Choose the best currently verified-free candidate without paid fallback."""
    ranked = [(_score(candidate, task), candidate) for candidate in candidates]
    ranked = [item for item in ranked if item[0][0] >= 0]
    if not ranked:
        return RouteDecision(None, 0.0, ("no verified-free model available",))
    (score, reasons), candidate = max(ranked, key=lambda item: (item[0][0], item[1].provider, item[1].model))
    return RouteDecision(candidate, score, tuple(reasons))
