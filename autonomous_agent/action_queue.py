from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


class ActionStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ActionProposal:
    task: str
    steps: tuple[str, ...]
    requires_approval: bool
    status: ActionStatus
    reason: str


@dataclass(frozen=True)
class PendingAction:
    id: str
    task: str
    steps: tuple[str, ...]
    risk: str
    reason: str
    status: str = "pending"


def _sensitive(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "write", "modify", "change code", "edit", "delete", "remove",
        "merge", "deploy", "release", "credential", "secret", "token",
        "password", "billing", "payment", "production", "destructive",
    )
    return any(marker in lowered for marker in markers)


def build_action_proposal(task: str, steps: tuple[str, ...], llm_requires_approval: bool = False) -> ActionProposal:
    """Create a conservative action boundary; model output can only increase risk, never reduce it."""
    task_risky = _sensitive(task)
    steps_risky = any(_sensitive(step) for step in steps)
    requires = bool(llm_requires_approval or task_risky or steps_risky)
    if requires:
        return ActionProposal(task, steps[:12], True, ActionStatus.PROPOSED,
                              "Approval is required because the task or proposed steps cross a sensitive action boundary.")
    return ActionProposal(task, steps[:12], False, ActionStatus.COMPLETED,
                          "Only bounded non-sensitive actions were proposed.")


def _id(proposal: ActionProposal, risk: str) -> str:
    raw = json.dumps([proposal.task, proposal.steps, risk], separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_queue(path: Path) -> list[PendingAction]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            result.append(PendingAction(str(item["id"]), str(item["task"]), tuple(item.get("steps", [])),
                                       str(item["risk"]), str(item["reason"]), str(item.get("status", "pending"))))
        except (KeyError, TypeError):
            continue
    return result


def enqueue_proposal(path: Path, proposal: ActionProposal, risk: str = "medium") -> PendingAction | None:
    if not proposal.requires_approval:
        return None
    action = PendingAction(_id(proposal, risk), proposal.task, proposal.steps, risk, proposal.reason)
    queue = load_queue(path)
    if any(item.id == action.id and item.status == "pending" for item in queue):
        return action
    queue.append(action)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(item) for item in queue], indent=2) + "\n", encoding="utf-8")
    return action
