from __future__ import annotations

import json
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
    """Return a fail-closed recovery decision from trusted persisted lifecycle state."""
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
        return RecoveryDecision(True, "execution state may have been interrupted; recovery must block", state)
    return RecoveryDecision(False, "lifecycle is not in an interrupted execution state", state)


def recover_stale_execution(path: Path, action_id: str) -> RecoveryDecision:
    """Convert interrupted execution into a terminal blocked state.

    Recovery never resumes execution and never reuses an approval claim. It only
    records a fail-closed terminal state after validating the ledger.
    """
    decision = inspect_recovery(path, action_id)
    if not decision.recoverable:
        return decision
    try:
        append_transition(path, action_id, decision.state, LifecycleState.BLOCKED)
    except (OSError, TypeError, ValueError) as exc:
        return RecoveryDecision(False, f"recovery transition was not persisted: {exc}", LifecycleState.BLOCKED)
    return RecoveryDecision(False, "interrupted execution was terminally blocked", LifecycleState.BLOCKED)


def write_recovery_record(path: Path, action_id: str, state: LifecycleState, record_path: Path) -> bool:
    """Persist metadata-only recovery evidence without secrets or raw approval tokens."""
    try:
        record = {"action_id": action_id, "state": state.value}
        record_path.parent.mkdir(parents=True, exist_ok=True)
        with record_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except (OSError, TypeError, ValueError):
        return False
    return True
