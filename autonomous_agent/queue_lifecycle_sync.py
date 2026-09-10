from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .action_lifecycle import LifecycleState
from .action_queue import load_queue
from .lifecycle_ledger import action_state, verify_ledger


@dataclass(frozen=True)
class SyncDecision:
    action_id: str
    queue_status: str
    lifecycle_state: LifecycleState | None
    decision: str
    reason: str


def reconcile_queue_lifecycle(queue_path: Path, lifecycle_path: Path) -> tuple[SyncDecision, ...]:
    """Detect queue/ledger drift without mutating queue state or executing work."""
    queue = load_queue(queue_path)
    if not lifecycle_path.exists():
        return tuple(SyncDecision(item.id, item.status, None, "unchanged", "no lifecycle ledger exists") for item in queue)
    if not verify_ledger(lifecycle_path):
        return tuple(SyncDecision(item.id, item.status, None, "blocked", "lifecycle ledger is invalid") for item in queue)

    decisions: list[SyncDecision] = []
    for item in queue:
        state = action_state(lifecycle_path, item.id)
        if state is None:
            decisions.append(SyncDecision(item.id, item.status, None, "drift", "queue action has no lifecycle history"))
        elif state is LifecycleState.COMPLETED and item.status != "completed":
            decisions.append(SyncDecision(item.id, item.status, state, "drift", "completed lifecycle is not reflected in queue"))
        elif state is LifecycleState.BLOCKED and item.status != "blocked":
            decisions.append(SyncDecision(item.id, item.status, state, "drift", "blocked lifecycle is not reflected in queue"))
        elif state in {LifecycleState.CLAIMED, LifecycleState.EXECUTED} and item.status != "blocked":
            decisions.append(SyncDecision(item.id, item.status, state, "drift", "interrupted lifecycle requires terminal blocking"))
        else:
            decisions.append(SyncDecision(item.id, item.status, state, "consistent", "queue and lifecycle state agree"))
    return tuple(decisions)


def has_blocking_drift(decisions: tuple[SyncDecision, ...]) -> bool:
    return any(item.decision in {"blocked", "drift"} for item in decisions)
