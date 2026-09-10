from __future__ import annotations

from pathlib import Path

from .action_lifecycle import LifecycleState
from .lifecycle_ledger import action_state, append_transition, verify_ledger


def require_state(path: Path, action_id: str, expected: LifecycleState | str) -> tuple[bool, str]:
    """Require a trusted persisted lifecycle state before a protected action."""
    try:
        expected_state = LifecycleState(expected)
    except (TypeError, ValueError):
        return False, "expected lifecycle state is invalid"
    if not path.exists():
        return False, "lifecycle ledger is required"
    if not verify_ledger(path):
        return False, "lifecycle ledger is invalid"
    current = action_state(path, action_id)
    if current is None:
        return False, "lifecycle history for action is missing"
    if current != expected_state:
        return False, f"lifecycle state is {current.value}; expected {expected_state.value}"
    return True, "lifecycle state is trusted"


def record_transition(path: Path, action_id: str, current: LifecycleState | str, target: LifecycleState | str) -> tuple[bool, str]:
    """Persist exactly one allowed transition; any ledger failure blocks progression."""
    try:
        if not verify_ledger(path):
            return False, "lifecycle ledger is invalid"
        append_transition(path, action_id, current, target)
    except (OSError, TypeError, ValueError) as exc:
        return False, f"lifecycle transition was not persisted: {exc}"
    return True, "lifecycle transition persisted"
