from __future__ import annotations

import hashlib
import json
import re
import os
import tempfile
from threading import RLock
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

    @classmethod
    def create(cls, action_id: str, task: str, steps, *, status: str = "pending", risk: str = "low", reason: str = "test action", created_at: str = ""):
        return cls(str(action_id), str(task), tuple(steps), str(risk), str(reason), str(status), str(created_at))


_SECRET = re.compile(r"(?i)(?:api[_-]?key|api\s+key|access[_-]?token|access\s+token|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----.*?-----END [A-Z0-9 ]+PRIVATE KEY-----", re.S)

def _safe_text(value: object, limit: int = 4096) -> str:
    text = _PRIVATE_KEY.sub("[REDACTED]", str(value))
    return " ".join(_SECRET.sub("[REDACTED]", text).split())[:limit]

RISK_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
MAX_PENDING_ACTIONS = 50
MAX_PENDING_AGE = timedelta(days=7)
_QUEUE_LOCK = RLock()
_VALID_STATUSES = {"pending", "approved", "rejected", "blocked", "completed"}
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


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
    safe_task = _safe_text(task)
    safe_steps = tuple(_safe_text(step, 2048) for step in steps[:12])
    if requires:
        return ActionProposal(safe_task, safe_steps, True, ActionStatus.PROPOSED,
                              "Approval is required because the task or proposed steps cross a sensitive action boundary.")
    return ActionProposal(safe_task, safe_steps, False, ActionStatus.PROPOSED,
                          "Only bounded non-sensitive actions were proposed; no execution has occurred.")


def _id(proposal: ActionProposal, risk: str) -> str:
    raw = json.dumps([proposal.task, proposal.steps, risk], separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _validate_loaded_action(item: PendingAction) -> None:
    if not _ID_RE.fullmatch(item.id):
        raise ValueError("approval queue contains an invalid action id")
    if item.risk.lower() not in RISK_PRIORITY:
        raise ValueError("approval queue contains an invalid risk")
    if item.status not in _VALID_STATUSES:
        raise ValueError("approval queue contains an invalid status")
    if item.task != _safe_text(item.task, 4096):
        raise ValueError("approval queue contains unsanitized task data")
    if len(item.steps) > 12 or any(not isinstance(step, str) or step != _safe_text(step, 2048) for step in item.steps):
        raise ValueError("approval queue contains unsanitized step data")
    if item.reason != _safe_text(item.reason, 2048):
        raise ValueError("approval queue contains unsanitized reason data")
    if item.created_at:
        try:
            created = datetime.fromisoformat(item.created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("approval queue contains an invalid creation timestamp") from exc
        if created.tzinfo is None:
            raise ValueError("approval queue creation timestamp must include a timezone")


def load_queue(path: Path) -> list[PendingAction]:
    with _QUEUE_LOCK:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("approval queue is unreadable") from exc
        if not isinstance(data, list):
            raise ValueError("approval queue has an invalid schema")
        result: list[PendingAction] = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("approval queue item is invalid")
            try:
                action = PendingAction(
                    str(item["id"]),
                    str(item["task"]),
                    tuple(item.get("steps", [])),
                    str(item["risk"]),
                    str(item["reason"]),
                    str(item.get("status", "pending")),
                    str(item.get("created_at", "")),
                )
            except (KeyError, TypeError) as exc:
                raise ValueError("approval queue item is invalid") from exc
            _validate_loaded_action(action)
            result.append(action)
        return result


def save_queue(path: Path, queue: list[PendingAction]) -> None:
    """Persist the approval queue atomically after validating every record."""
    with _QUEUE_LOCK:
        for item in queue:
            _validate_loaded_action(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump([asdict(item) for item in queue], handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except OSError as exc:
            raise OSError("approval queue could not be persisted atomically") from exc
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass


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
    action = PendingAction(_id(proposal, normalized_risk), _safe_text(proposal.task), tuple(_safe_text(step, 2048) for step in proposal.steps[:12]), normalized_risk,
                           _safe_text(proposal.reason, 2048), "pending", datetime.now(timezone.utc).isoformat())
    with _QUEUE_LOCK:
        queue = expire_stale_actions(load_queue(path))
    for item in queue:
        if item.id == action.id and item.status == "pending":
            return item
    queue.append(action)
    pending = prioritize_queue([item for item in queue if item.status == "pending"])
    non_pending = [item for item in queue if item.status != "pending"]
    queue = pending[:MAX_PENDING_ACTIONS] + non_pending
        save_queue(path, queue)
    return action
