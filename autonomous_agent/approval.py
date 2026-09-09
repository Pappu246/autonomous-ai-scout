from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .action_queue import PendingAction, load_queue


VALID_DECISIONS = {"approved", "rejected"}
TERMINAL_STATUSES = {"approved", "rejected"}


def set_decision(path: Path, action_id: str, decision: str) -> PendingAction:
    """Record an explicit approval decision; this never executes the action."""
    decision = decision.strip().lower()
    if decision not in VALID_DECISIONS:
        raise ValueError("decision must be 'approved' or 'rejected'")
    queue = load_queue(path)
    for index, action in enumerate(queue):
        if action.id == action_id:
            if action.status in TERMINAL_STATUSES:
                raise ValueError(f"approval action is already {action.status}")
            updated = PendingAction(action.id, action.task, action.steps, action.risk, action.reason, decision)
            queue[index] = updated
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([asdict(item) for item in queue], indent=2) + "\n", encoding="utf-8")
            return updated
    raise KeyError(f"approval action not found: {action_id}")


def pending_actions(path: Path) -> list[PendingAction]:
    return [item for item in load_queue(path) if item.status == "pending"]
