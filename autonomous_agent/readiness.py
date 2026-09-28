from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: tuple[ReadinessCheck, ...]


def evaluate_readiness(checks: list[ReadinessCheck]) -> ReadinessReport:
    bounded = tuple(checks[:64])
    return ReadinessReport(bool(bounded) and all(item.passed for item in bounded), bounded)


__all__ = ["ReadinessCheck", "ReadinessReport", "evaluate_readiness"]
