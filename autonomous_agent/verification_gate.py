from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class GateStage(str, Enum):
    PROPOSAL = "proposal"
    VALIDATION = "validation"
    TESTS = "tests"
    SECURITY = "security"
    POLICY = "policy"
    APPROVAL = "approval"
    EXECUTION = "execution"
    POST_TESTS = "post_tests"
    RESULT_VERIFICATION = "result_verification"


ORDERED_STAGES: tuple[GateStage, ...] = (
    GateStage.PROPOSAL,
    GateStage.VALIDATION,
    GateStage.TESTS,
    GateStage.SECURITY,
    GateStage.POLICY,
    GateStage.APPROVAL,
    GateStage.EXECUTION,
    GateStage.POST_TESTS,
    GateStage.RESULT_VERIFICATION,
)


@dataclass(frozen=True)
class GateCheck:
    stage: GateStage
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateResult:
    passed: bool
    checks: tuple[GateCheck, ...]
    stopped_at: GateStage | None
    reason: str
    execution_attempted: bool


def _check(stage: GateStage, callback: Callable[[], bool | str]) -> GateCheck:
    try:
        value = callback()
    except Exception as exc:  # gate must fail closed on validator errors
        return GateCheck(stage, False, f"gate callback failed: {type(exc).__name__}")
    if isinstance(value, str):
        return GateCheck(stage, bool(value.strip()), value.strip() or "failed")
    return GateCheck(stage, bool(value), "passed" if value else "failed")


def run_verification_gate(
    *,
    proposal: Callable[[], bool | str],
    validation: Callable[[], bool | str],
    tests: Callable[[], bool | str],
    security: Callable[[], bool | str],
    policy: Callable[[], bool | str],
    approval: Callable[[], bool | str],
    execution: Callable[[], bool | str],
    post_tests: Callable[[], bool | str],
    result_verification: Callable[[], bool | str],
) -> GateResult:
    """Run the complete deterministic progression gate and stop at the first failure.

    Callbacks are invoked strictly in order. Execution is unreachable unless proposal,
    validation, tests, security, policy, and approval all pass.
    """
    callbacks = {
        GateStage.PROPOSAL: proposal,
        GateStage.VALIDATION: validation,
        GateStage.TESTS: tests,
        GateStage.SECURITY: security,
        GateStage.POLICY: policy,
        GateStage.APPROVAL: approval,
        GateStage.EXECUTION: execution,
        GateStage.POST_TESTS: post_tests,
        GateStage.RESULT_VERIFICATION: result_verification,
    }
    checks: list[GateCheck] = []

    for stage in ORDERED_STAGES:
        check = _check(stage, callbacks[stage])
        checks.append(check)
        if not check.passed:
            return GateResult(
                passed=False,
                checks=tuple(checks),
                stopped_at=stage,
                reason=check.detail,
                execution_attempted=any(item.stage == GateStage.EXECUTION for item in checks),
            )

    return GateResult(
        passed=True,
        checks=tuple(checks),
        stopped_at=None,
        reason="all verification stages passed",
        execution_attempted=True,
    )
