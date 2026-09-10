from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
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
    created_at: str = ""


RISK_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
MAX_PENDING_ACTIONS = 50
MAX_PENDING_AGE = timedelta(days=7)


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
                                       str(item["risk"]), str(item["reason"]), str(item.get("status", "pending")),
                                       str(item.get("created_at", ""))))
        except (KeyError, TypeError):
            continue
    return result


def _is_stale(item: PendingAction, now: datetime) -> bool:
    if item.status != "pending" or not item.created_at:
        return False
    try:
        created = datetime.fromisoformat(item.created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return now - created > MAX_PENDING_AGE


def expire_stale_actions(queue: list[PendingAction], now: datetime | None = None) -> list[PendingAction]:
    """Mark stale pending actions blocked; never auto-approves or executes them."""
    current = now or datetime.now(timezone.utc)
    return [
        PendingAction(item.id, item.task, item.steps, item.risk, item.reason,
                      "blocked" if _is_stale(item, current) else item.status, item.created_at)
        for item in queue
    ]


def prioritize_queue(queue: list[PendingAction]) -> list[PendingAction]:
    """Return pending actions in risk-first order without changing their approval state."""
    return sorted(queue, key=lambda item: (RISK_PRIORITY.get(item.risk.lower(), 2), item.id))


def enqueue_proposal(path: Path, proposal: ActionProposal, risk: str = "medium") -> PendingAction | None:
    if not proposal.requires_approval:
        return None
    normalized_risk = risk.lower() if risk.lower() in RISK_PRIORITY else "medium"
    action = PendingAction(_id(proposal, normalized_risk), proposal.task, proposal.steps, normalized_risk,
                           proposal.reason, "pending", datetime.now(timezone.utc).isoformat())
    queue = expire_stale_actions(load_queue(path))
    for item in queue:
        if item.id == action.id and item.status == "pending":
            return item
    queue.append(action)
    pending = prioritize_queue([item for item in queue if item.status == "pending"])
    non_pending = [item for item in queue if item.status != "pending"]
    queue = pending[:MAX_PENDING_ACTIONS] + non_pending
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(item) for item in queue], indent=2) + "\n", encoding="utf-8")
    return action
