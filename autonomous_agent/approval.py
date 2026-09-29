from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .action_queue import PendingAction, load_queue
from .approval_audit import append_decision
from .file_lock import InterProcessFileLock


VALID_DECISIONS = {"approved", "rejected"}
TERMINAL_STATUSES = {"approved", "rejected"}


def set_decision(path: Path, action_id: str, decision: str, audit_path: Path | None = None) -> PendingAction:
    """Record an explicit approval decision under the same process lock as the queue store."""
    decision = decision.strip().lower()
    if decision not in VALID_DECISIONS:
        raise ValueError("decision must be 'approved' or 'rejected'")

    process_lock = InterProcessFileLock(path.with_name(path.name + ".lock"))
    with process_lock:
        queue = load_queue(path)
        for index, action in enumerate(queue):
            if action.id != action_id:
                continue
            if action.status in TERMINAL_STATUSES:
                raise ValueError(f"approval action is already {action.status}")
            updated = PendingAction(
                action.id,
                action.task,
                action.steps,
                action.risk,
                action.reason,
                decision,
                action.created_at,
            )
            queue[index] = updated
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=path.name + ".",
                suffix=".tmp",
                dir=str(path.parent),
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump([asdict(item) for item in queue], handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                temporary = None
            finally:
                if temporary:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass
            if audit_path is not None:
                append_decision(audit_path, action_id, decision)
            return updated
    raise KeyError(f"approval action not found: {action_id}")


def pending_actions(path: Path) -> list[PendingAction]:
    return [item for item in load_queue(path) if item.status == "pending"]
