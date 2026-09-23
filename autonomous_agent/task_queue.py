from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Iterable


class QueueState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RECOVERY_REQUIRED = "recovery_required"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class QueueItem:
    task_id: str
    task: str
    execution_id: str
    state: QueueState
    created_at: str
    updated_at: str
    available_at: str
    attempts: int = 0
    last_error: str = ""

    @property
    def ready(self) -> bool:
        return _parse_time(self.available_at) <= datetime.now(timezone.utc)


_TASK_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|authorization|token|secret|password|credential)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----"),
)

def _safe_task(value: str) -> str:
    text = str(value)
    for pattern in _TASK_SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]" if match.lastindex else "[REDACTED]", text)
    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("queued task must be non-empty")
    return normalized[:4000]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class TaskQueueStore:
    """Durable single-file task queue with atomic snapshots."""

    def __init__(self, path: str | Path = "state/task_queue.json") -> None:
        self.path = Path(path)
        self._lock = Lock()

    def _load_unlocked(self) -> list[QueueItem]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("task queue is unreadable") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("task queue has an invalid schema")
        items: list[QueueItem] = []
        for item in payload["items"]:
            if not isinstance(item, dict):
                raise ValueError("task queue item is invalid")
            queue_item = QueueItem(
                str(item["task_id"]),
                str(item["task"]),
                str(item["execution_id"]),
                QueueState(str(item["state"])),
                str(item["created_at"]),
                str(item["updated_at"]),
                str(item["available_at"]),
                int(item.get("attempts", 0)),
                str(item.get("last_error", "")),
            )
            self._validate_item(queue_item)
            items.append(queue_item)
        return items

    def _save_unlocked(self, items: Iterable[QueueItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            payload = {
                "items": [
                    {
                        "attempts": item.attempts,
                        "available_at": item.available_at,
                        "created_at": item.created_at,
                        "execution_id": item.execution_id,
                        "last_error": item.last_error,
                        "state": item.state.value,
                        "task": item.task,
                        "task_id": item.task_id,
                        "updated_at": item.updated_at,
                    }
                    for item in items
                ]
            }
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp_name, self.path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def list(self) -> tuple[QueueItem, ...]:
        with self._lock:
            return tuple(self._load_unlocked())

    def enqueue(
        self, task: str, *, task_id: str, execution_id: str, available_at: str | None = None
    ) -> QueueItem:
        normalized = _safe_task(task)
        task_id = task_id.strip()
        execution_id = execution_id.strip()
        if not task_id or not execution_id or len(task_id) > 128 or len(execution_id) > 128:
            raise ValueError("task_id and execution_id are required and bounded")
        scheduled = available_at or _now()
        self._validate_timestamp(scheduled, "available_at")
        with self._lock:
            items = self._load_unlocked()
            if any(item.task_id == task_id for item in items):
                raise ValueError("task_id already exists")
            now = _now()
            item = QueueItem(task_id, normalized, execution_id, QueueState.PENDING, now, now, scheduled)
            self._validate_item(item)
            self._save_unlocked((*items, item))
            return item

    def recover_running(self) -> tuple[QueueItem, ...]:
        with self._lock:
            items = self._load_unlocked()
            recovered: list[QueueItem] = []
            changed = False
            for item in items:
                if item.state is QueueState.RUNNING:
                    recovered.append(QueueItem(item.task_id, item.task, item.execution_id, QueueState.RECOVERY_REQUIRED, item.created_at, _now(), item.available_at, item.attempts, "worker restarted while task was running; explicit recovery confirmation required"))
                    changed = True
                else:
                    recovered.append(item)
            if changed:
                self._save_unlocked(recovered)
            return tuple(item for item in recovered if item.state is QueueState.RECOVERY_REQUIRED)

    def confirm_recovery(self, task_id: str) -> QueueItem:
        with self._lock:
            items = self._load_unlocked()
            for index, item in enumerate(items):
                if item.task_id == task_id:
                    if item.state is not QueueState.RECOVERY_REQUIRED:
                        raise ValueError("task is not awaiting recovery")
                    updated = QueueItem(item.task_id, item.task, item.execution_id, QueueState.PENDING, item.created_at, _now(), item.available_at, item.attempts, "recovery explicitly confirmed")
                    items[index] = updated
                    self._save_unlocked(items)
                    return updated
        raise KeyError(task_id)

    def claim_next(self) -> QueueItem | None:
        with self._lock:
            items = self._load_unlocked()
            for index, item in enumerate(items):
                if item.state is QueueState.PENDING and item.ready:
                    claimed = QueueItem(item.task_id, item.task, item.execution_id, QueueState.RUNNING, item.created_at, _now(), item.available_at, item.attempts + 1, "")
                    items[index] = claimed
                    self._save_unlocked(items)
                    return claimed
            return None

    def complete(self, task_id: str, *, success: bool, error: str = "") -> QueueItem:
        with self._lock:
            items = self._load_unlocked()
            for index, item in enumerate(items):
                if item.task_id == task_id:
                    if item.state is not QueueState.RUNNING:
                        raise ValueError("only a running task can be completed")
                    state = QueueState.SUCCEEDED if success else QueueState.FAILED
                    updated = QueueItem(item.task_id, item.task, item.execution_id, state, item.created_at, _now(), item.available_at, item.attempts, error[:500])
                    items[index] = updated
                    self._save_unlocked(items)
                    return updated
        raise KeyError(task_id)

    def cancel(self, task_id: str) -> QueueItem:
        with self._lock:
            items = self._load_unlocked()
            for index, item in enumerate(items):
                if item.task_id == task_id:
                    if item.state not in {QueueState.PENDING, QueueState.RECOVERY_REQUIRED}:
                        raise ValueError("only pending or recovery-required tasks can be cancelled")
                    updated = QueueItem(item.task_id, item.task, item.execution_id, QueueState.CANCELLED, item.created_at, _now(), item.available_at, item.attempts, item.last_error)
                    items[index] = updated
                    self._save_unlocked(items)
                    return updated
        raise KeyError(task_id)


__all__ = ["QueueItem", "QueueState", "TaskQueueStore"]
