from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .action_lifecycle import LifecycleState
from .action_queue import PendingAction, expire_stale_actions, load_queue, prioritize_queue
from .lifecycle_ledger import action_state, verify_ledger
from .lifecycle_recovery import recover_stale_execution


@dataclass(frozen=True)
class ReconciliationDecision:
    action_id: str
    state: LifecycleState
    action_status: str
    decision: str
    reason: str


def reconcile_startup(
    queue_path: Path,
    lifecycle_path: Path,
    *,
    auto_block_interrupted: bool = True,
) -> tuple[ReconciliationDecision, ...]:
    """Reconcile persisted actions at startup without resuming any work.

    Missing/corrupt lifecycle state is fail-closed. Interrupted execution states
    are terminally blocked when requested. Pending queue entries are only aged;
    they are never auto-approved or executed.
    """
    queue = expire_stale_actions(load_queue(queue_path))
    if lifecycle_path.exists() and not verify_ledger(lifecycle_path):
        return tuple(
            ReconciliationDecision(item.id, LifecycleState.BLOCKED, item.status, "blocked", "lifecycle ledger is invalid")
            for item in queue
            if item.status != "blocked"
        )

    decisions: list[ReconciliationDecision] = []
    seen: set[str] = set()
    for item in prioritize_queue(queue):
        seen.add(item.id)
        state = action_state(lifecycle_path, item.id) if lifecycle_path.exists() else None
        if state in {LifecycleState.CLAIMED, LifecycleState.EXECUTED}:
            if auto_block_interrupted:
                recovered = recover_stale_execution(lifecycle_path, item.id)
                decisions.append(
                    ReconciliationDecision(item.id, recovered.state, item.status, "blocked", recovered.reason)
                )
            else:
                decisions.append(
                    ReconciliationDecision(item.id, state, item.status, "attention_required", "interrupted execution requires recovery")
                )
            continue
        if state in {LifecycleState.BLOCKED, LifecycleState.COMPLETED}:
            decisions.append(
                ReconciliationDecision(item.id, state, item.status, "terminal", "persisted lifecycle is terminal")
            )
            continue
        if state is None:
            if item.status == "pending" and lifecycle_path.exists():
                decisions.append(
                    ReconciliationDecision(item.id, LifecycleState.BLOCKED, item.status, "blocked", "queue action has no trusted lifecycle history")
                )
            else:
                decisions.append(
                    ReconciliationDecision(item.id, LifecycleState.PROPOSED, item.status, "unchanged", "no lifecycle history is present")
                )
            continue
        decisions.append(
            ReconciliationDecision(item.id, state, item.status, "unchanged", "startup reconciliation made no execution decision")
        )

    if lifecycle_path.exists():
        # Detect lifecycle actions that are not represented in the queue. They are
        # never resumed; the persisted state remains the source of truth for audit.
        tracked_ids = {event_id for event_id in _lifecycle_action_ids(lifecycle_path)}
        for action_id in sorted(tracked_ids - seen):
            state = action_state(lifecycle_path, action_id)
            if state in {LifecycleState.CLAIMED, LifecycleState.EXECUTED} and auto_block_interrupted:
                recovered = recover_stale_execution(lifecycle_path, action_id)
                decisions.append(
                    ReconciliationDecision(action_id, recovered.state, "missing", "blocked", recovered.reason)
                )
    return tuple(decisions)


def _lifecycle_action_ids(path: Path) -> tuple[str, ...]:
    """Read action ids only after the caller verified ledger integrity."""
    try:
        ids = {
            line.split('"action_id":"', 1)[1].split('"', 1)[0]
            for line in path.read_text(encoding="utf-8").splitlines()
            if '"action_id":"' in line
        }
    except (OSError, IndexError):
        return ()
    return tuple(sorted(ids))
