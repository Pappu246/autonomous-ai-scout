from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class EvaluationCheck:
    name: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class EvaluationReport:
    passed: bool
    score: float
    checks: tuple[EvaluationCheck, ...]
    reason: str


def evaluate_execution(
    *,
    expected_operations: Iterable[str],
    actual_operations: Iterable[str],
    verification_statuses: Iterable[str],
) -> EvaluationReport:
    expected = tuple(str(item).strip() for item in expected_operations if str(item).strip())
    actual = tuple(str(item).strip() for item in actual_operations if str(item).strip())
    verified = tuple(str(item).strip().lower() for item in verification_statuses)
    checks = (
        EvaluationCheck("operations", expected == actual, f"expected={expected}; actual={actual}"),
        EvaluationCheck("verification", bool(verified) and all(item == "verified" for item in verified), f"statuses={verified}"),
    )
    passed = all(item.passed for item in checks)
    score = 1.0 if passed else sum(item.passed for item in checks) / len(checks)
    reason = "all deterministic execution checks passed" if passed else "one or more deterministic execution checks failed"
    return EvaluationReport(passed, score, checks, reason)


__all__ = ["EvaluationCheck", "EvaluationReport", "evaluate_execution"]
