from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    input_value: object
    expected: object


@dataclass(frozen=True)
class BenchmarkObservation:
    case: str
    passed: bool
    latency_ms: int
    error_type: str | None = None


@dataclass(frozen=True)
class BenchmarkReport:
    version: str
    observations: tuple[BenchmarkObservation, ...]
    score: float


def run_benchmark(
    cases: Iterable[BenchmarkCase],
    runner: Callable[[object], object],
    *,
    version: str = "1",
    max_cases: int = 100,
) -> BenchmarkReport:
    observations: list[BenchmarkObservation] = []
    for index, case in enumerate(cases):
        if index >= max_cases:
            break
        started = time.perf_counter()
        try:
            actual = runner(case.input_value)
            passed = actual == case.expected
            error_type = None
        except Exception as exc:
            passed = False
            error_type = type(exc).__name__
        latency = int((time.perf_counter() - started) * 1000)
        observations.append(BenchmarkObservation(str(case.name)[:256], passed, latency, error_type))
    score = sum(item.passed for item in observations) / len(observations) if observations else 0.0
    return BenchmarkReport(str(version)[:64], tuple(observations), score)


__all__ = ["BenchmarkCase", "BenchmarkObservation", "BenchmarkReport", "run_benchmark"]
