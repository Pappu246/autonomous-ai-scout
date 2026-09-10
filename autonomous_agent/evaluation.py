from __future__ import annotations

from dataclasses import dataclass

from .benchmark import BenchmarkResult


@dataclass(frozen=True)
class EvaluationScore:
    provider: str
    model: str
    score: float
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]


def score_benchmark(result: BenchmarkResult) -> EvaluationScore:
    """Score a free-tier benchmark without retrying or changing access/billing state."""
    if not result.attempted:
        return EvaluationScore(
            result.provider,
            result.model,
            0.0,
            (),
            ("benchmark not attempted",),
        )

    score = 50.0
    strengths: list[str] = []
    weaknesses: list[str] = []

    if result.success:
        score += 35.0
        strengths.append("benchmark passed")
    else:
        score -= 35.0
        weaknesses.append("benchmark failed")

    if result.latency_ms is not None:
        if result.latency_ms < 1000:
            score += 15.0
            strengths.append("low latency")
        elif result.latency_ms > 5000:
            score -= 15.0
            weaknesses.append("high latency")

    if "rate limited" in result.note.lower():
        weaknesses.append("rate limited")

    return EvaluationScore(
        result.provider,
        result.model,
        max(0.0, min(100.0, score)),
        tuple(strengths),
        tuple(weaknesses),
    )


def rank_benchmarks(results: list[BenchmarkResult]) -> list[EvaluationScore]:
    """Rank only the supplied benchmark results deterministically."""
    scored = [score_benchmark(result) for result in results]
    return sorted(scored, key=lambda item: (-item.score, item.provider, item.model))
