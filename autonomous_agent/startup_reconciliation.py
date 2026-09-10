from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .action_lifecycle import LifecycleState
from .action_queue import expire_stale_actions, load_queue, prioritize_queue
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
    """Reconcile persisted actions at startup without resuming any work."""
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
                decisions.append(ReconciliationDecision(item.id, recovered.state, item.status, "blocked", recovered.reason))
            else:
                decisions.append(ReconciliationDecision(item.id, state, item.status, "attention_required", "interrupted execution requires recovery"))
            continue
        if state in {LifecycleState.BLOCKED, LifecycleState.COMPLETED}:
            decisions.append(ReconciliationDecision(item.id, state, item.status, "terminal", "persisted lifecycle is terminal"))
            continue
        if state is None:
            if item.status == "pending" and lifecycle_path.exists():
                decisions.append(ReconciliationDecision(item.id, LifecycleState.BLOCKED, item.status, "blocked", "queue action has no trusted lifecycle history"))
            else:
                decisions.append(ReconciliationDecision(item.id, LifecycleState.PROPOSED, item.status, "unchanged", "no lifecycle history is present"))
            continue
        decisions.append(ReconciliationDecision(item.id, state, item.status, "unchanged", "startup reconciliation made no execution decision"))

    if lifecycle_path.exists():
        for action_id in sorted(set(_lifecycle_action_ids(lifecycle_path)) - seen):
            state = action_state(lifecycle_path, action_id)
            if state in {LifecycleState.CLAIMED, LifecycleState.EXECUTED} and auto_block_interrupted:
                recovered = recover_stale_execution(lifecycle_path, action_id)
                decisions.append(ReconciliationDecision(action_id, recovered.state, "missing", "blocked", recovered.reason))
    return tuple(decisions)


def _lifecycle_action_ids(path: Path) -> tuple[str, ...]:
    """Read action ids from verified JSONL without depending on JSON whitespace."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    ids: set[str] = set()
    try:
        for line in lines:
            if not line.strip():
                continue
            event = json.loads(line)
            if isinstance(event, dict) and isinstance(event.get("action_id"), str) and event["action_id"].strip():
                ids.add(event["action_id"])
    except (TypeError, ValueError):
        return ()
    return tuple(sorted(ids))
