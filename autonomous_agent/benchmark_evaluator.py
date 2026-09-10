from __future__ import annotations

from dataclasses import dataclass
from .benchmark import BenchmarkResult


@dataclass(frozen=True)
class Evaluation:
    score: float
    reasons: tuple[str, ...]


def evaluate(result: BenchmarkResult) -> Evaluation:
    """Score a completed free-tier benchmark without retrying or changing access."""
    if not result.attempted:
        return Evaluation(0.0, ("benchmark not attempted",))
    if not result.success:
        if "Rate limited" in result.note:
            return Evaluation(20.0, ("rate limited; no paid retry",))
        return Evaluation(10.0, ("benchmark failed",))

    score = 70.0
    reasons = ["benchmark succeeded"]
    if result.latency_ms is not None:
        if result.latency_ms < 1000:
            score += 30
            reasons.append("latency under 1s")
        elif result.latency_ms <= 5000:
            score += 15
            reasons.append("latency under 5s")
        else:
            score += 5
            reasons.append("latency above 5s")
    return Evaluation(min(100.0, score), tuple(reasons))


def evaluate_many(results: list[BenchmarkResult]) -> dict[tuple[str, str], Evaluation]:
    return {(r.provider, r.model): evaluate(r) for r in results}
