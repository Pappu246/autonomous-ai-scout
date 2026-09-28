from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RecoveryAction(str, Enum):
    RETRY = "retry"
    RECONCILE = "reconcile"
    ABORT = "abort"


@dataclass(frozen=True)
class RecoveryDirective:
    action: RecoveryAction
    reason: str
    retry_after_seconds: float = 0.0


def classify_failure(
    *,
    exception_type: str,
    attempt: int,
    max_attempts: int,
    side_effect_state: str | None = None,
) -> RecoveryDirective:
    if side_effect_state in {"reserved", "unknown"}:
        return RecoveryDirective(RecoveryAction.RECONCILE, "external side effect outcome is uncertain; automatic replay is unsafe")
    if attempt >= max_attempts:
        return RecoveryDirective(RecoveryAction.ABORT, "bounded retry budget exhausted")
    retryable = exception_type in {"TimeoutError", "ConnectionError", "TemporaryFailure"}
    if retryable:
        return RecoveryDirective(RecoveryAction.RETRY, "transient failure within bounded retry budget", retry_after_seconds=min(30.0, 2.0 ** attempt))
    return RecoveryDirective(RecoveryAction.ABORT, "failure is not classified as safely retryable")


__all__ = ["RecoveryAction", "RecoveryDirective", "classify_failure"]
