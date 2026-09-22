from __future__ import annotations

import json
import os
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
            items.append(
                QueueItem(
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
            )
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
        normalized = " ".join(task.strip().split())
        if not normalized:
            raise ValueError("queued task must be non-empty")
        if not task_id.strip() or not execution_id.strip():
            raise ValueError("task_id and execution_id are required")
        with self._lock:
            items = self._load_unlocked()
            if any(item.task_id == task_id for item in items):
                raise ValueError("task_id already exists")
            now = _now()
            item = QueueItem(task_id, normalized, execution_id, QueueState.PENDING, now, now, available_at or now)
            self._save_unlocked((*items, item))
            return item

    def recover_running(self) -> tuple[QueueItem, ...]:
        with self._lock:
            items = self._load_unlocked()
            recovered: list[QueueItem] = []
            changed = False
            for item in items:
                if item.state is QueueState.RUNNING:
                    recovered.append(QueueItem(item.task_id, item.task, item.execution_id, QueueState.PENDING, item.created_at, _now(), item.available_at, item.attempts, "worker restarted while task was running"))
                    changed = True
                else:
                    recovered.append(item)
            if changed:
                self._save_unlocked(recovered)
            return tuple(item for item in recovered if item.state is QueueState.PENDING and item.last_error)

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
                    updated = QueueItem(item.task_id, item.task, item.execution_id, QueueState.CANCELLED, item.created_at, _now(), item.available_at, item.attempts, item.last_error)
                    items[index] = updated
                    self._save_unlocked(items)
                    return updated
        raise KeyError(task_id)


__all__ = ["QueueItem", "QueueState", "TaskQueueStore"]
