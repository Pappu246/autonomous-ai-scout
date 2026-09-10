from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .action_lifecycle import LifecycleState
from .lifecycle_ledger import action_state, append_transition, verify_ledger


@dataclass(frozen=True)
class RecoveryDecision:
    recoverable: bool
    reason: str
    state: LifecycleState


def inspect_recovery(path: Path, action_id: str) -> RecoveryDecision:
    """Inspect persisted lifecycle state and fail closed on missing/corrupt history."""
    if not path.exists():
        return RecoveryDecision(False, "lifecycle ledger is required for recovery", LifecycleState.BLOCKED)
    if not verify_ledger(path):
        return RecoveryDecision(False, "lifecycle ledger is invalid", LifecycleState.BLOCKED)
    state = action_state(path, action_id)
    if state is None:
        return RecoveryDecision(False, "lifecycle history for action is missing", LifecycleState.BLOCKED)
    if state in {LifecycleState.BLOCKED, LifecycleState.COMPLETED}:
        return RecoveryDecision(False, "lifecycle is terminal; recovery is not allowed", state)
    if state in {LifecycleState.CLAIMED, LifecycleState.EXECUTED}:
        return RecoveryDecision(True, "execution may have been interrupted; recovery must block", state)
    return RecoveryDecision(False, "lifecycle is not in an interrupted execution state", state)


def recover_stale_execution(path: Path, action_id: str) -> RecoveryDecision:
    """Convert interrupted execution to terminal blocked state without resuming work."""
    decision = inspect_recovery(path, action_id)
    if not decision.recoverable:
        return decision
    try:
        append_transition(path, action_id, decision.state, LifecycleState.BLOCKED)
    except (OSError, TypeError, ValueError) as exc:
        return RecoveryDecision(False, f"recovery transition was not persisted: {exc}", LifecycleState.BLOCKED)
    return RecoveryDecision(False, "interrupted execution was terminally blocked", LifecycleState.BLOCKED)
