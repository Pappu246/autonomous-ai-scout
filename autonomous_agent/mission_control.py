from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .background_worker import BackgroundTaskWorker
from .execution_engine import ExecutionState
from .file_lock import InterProcessFileLock
from .task_core import AutonomousTaskCore
from .task_queue import QueueItem, QueueState, TaskQueueStore
from .runtime import run_task


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


@dataclass(frozen=True)
class MissionRecord:
    mission_id: str
    task_id: str
    execution_id: str
    task: str
    state: str
    reason: str
    created_at: str
    updated_at: str
    attempts: int = 0

    @property
    def terminal(self) -> bool:
        return self.state in {
            "blocked",
            QueueState.SUCCEEDED.value,
            QueueState.FAILED.value,
            QueueState.CANCELLED.value,
        }


class MissionStore:
    """Durable, secret-safe mission metadata layered over the existing task queue."""

    def __init__(self, path: str | Path = "state/missions.json") -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._process_lock = InterProcessFileLock(self.path.with_name(self.path.name + ".lock"))

    def _load_unlocked(self) -> list[MissionRecord]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("missions"), list):
            raise ValueError("mission store has an invalid schema")
        records: list[MissionRecord] = []
        for item in payload["missions"]:
            if not isinstance(item, dict):
                raise ValueError("mission record is invalid")
            records.append(
                MissionRecord(
                    mission_id=str(item["mission_id"]),
                    task_id=str(item["task_id"]),
                    execution_id=str(item["execution_id"]),
                    task=_bounded_text(item["task"], 4000),
                    state=str(item["state"]),
                    reason=_bounded_text(item.get("reason", ""), 1000),
                    created_at=str(item["created_at"]),
                    updated_at=str(item["updated_at"]),
                    attempts=int(item.get("attempts", 0)),
                )
            )
        return records

    def _save_unlocked(self, records: Iterable[MissionRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            payload = {"missions": [asdict(record) for record in records]}
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp_name, self.path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def list(self) -> tuple[MissionRecord, ...]:
        with self._lock, self._process_lock:
            records = self._load_unlocked()
        return tuple(sorted(records, key=lambda item: item.updated_at, reverse=True))

    def get(self, mission_id: str) -> MissionRecord | None:
        wanted = mission_id.strip()
        return next((item for item in self.list() if item.mission_id == wanted), None)

    def put(self, record: MissionRecord) -> MissionRecord:
        with self._lock, self._process_lock:
            records = self._load_unlocked()
            replaced = False
            updated: list[MissionRecord] = []
            for existing in records:
                if existing.mission_id == record.mission_id:
                    updated.append(record)
                    replaced = True
                else:
                    updated.append(existing)
            if not replaced:
                updated.append(record)
            self._save_unlocked(updated)
        return record


class MissionController:
    """Mission-level facade that composes the existing planner, queue, worker and runtime."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        queue_path: str | Path = "state/mission_queue.json",
        missions_path: str | Path = "state/missions.json",
        audit_path: str | Path = "state/runtime_execution.jsonl",
        journal_path: str | Path = "state/runtime_runs.jsonl",
    ) -> None:
        self.root = (root or Path.cwd()).resolve()
        self.queue = TaskQueueStore(self.root / queue_path)
        self.store = MissionStore(self.root / missions_path)
        self.audit_path = self.root / audit_path
        self.journal_path = self.root / journal_path
        self.core = AutonomousTaskCore()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.worker = BackgroundTaskWorker(
            self.queue,
            self._handle_queue_item,
            poll_interval=0.25,
        )

    def submit(self, task: str) -> MissionRecord:
        prepared = self.core.prepare(task)
        mission_id = f"mission-{uuid.uuid4().hex}"
        execution_id = f"exec-{uuid.uuid4().hex}"
        now = _now()

        if not prepared.plan.executable:
            record = MissionRecord(
                mission_id=mission_id,
                task_id=mission_id,
                execution_id=execution_id,
                task=prepared.task,
                state="blocked",
                reason=_bounded_text(prepared.plan.reason, 1000),
                created_at=now,
                updated_at=now,
            )
            return self.store.put(record)

        item = self.queue.enqueue(
            prepared.task,
            task_id=mission_id,
            execution_id=execution_id,
        )
        record = MissionRecord(
            mission_id=mission_id,
            task_id=item.task_id,
            execution_id=item.execution_id,
            task=item.task,
            state=item.state.value,
            reason="mission accepted into the durable background queue",
            created_at=item.created_at,
            updated_at=item.updated_at,
            attempts=item.attempts,
        )
        return self.store.put(record)

    def cancel(self, mission_id: str) -> MissionRecord:
        record = self.store.get(mission_id)
        if record is None:
            raise KeyError(mission_id)
        item = self.queue.cancel(record.task_id)
        return self.store.put(
            MissionRecord(
                record.mission_id,
                record.task_id,
                record.execution_id,
                record.task,
                item.state.value,
                "mission cancelled before execution",
                record.created_at,
                item.updated_at,
                item.attempts,
            )
        )

    def run_once(self) -> MissionRecord | None:
        item = self.worker.run_once()
        if item is None:
            return None
        return self.store.get(item.task_id)

    def recover(self) -> tuple[MissionRecord, ...]:
        items = self.worker.recover()
        recovered: list[MissionRecord] = []
        for item in items:
            existing = self.store.get(item.task_id)
            if existing is None:
                continue
            recovered.append(
                self.store.put(
                    MissionRecord(
                        existing.mission_id,
                        existing.task_id,
                        existing.execution_id,
                        existing.task,
                        item.state.value,
                        _bounded_text(item.last_error, 1000),
                        existing.created_at,
                        item.updated_at,
                        item.attempts,
                    )
                )
            )
        return tuple(recovered)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.recover()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.worker.run_forever,
            args=(self._stop,),
            name="autonomous-scout-mission-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def _handle_queue_item(self, item: QueueItem) -> bool:
        existing = self.store.get(item.task_id)
        if existing is not None:
            self.store.put(
                MissionRecord(
                    existing.mission_id,
                    existing.task_id,
                    existing.execution_id,
                    existing.task,
                    QueueState.RUNNING.value,
                    "mission is executing through the canonical runtime",
                    existing.created_at,
                    _now(),
                    item.attempts,
                )
            )

        try:
            result = run_task(
                item.task,
                root=self.root,
                audit_path=self.audit_path,
                journal_path=self.journal_path,
                execution_id=item.execution_id,
            )
        except Exception as exc:
            if existing is not None:
                self.store.put(
                    MissionRecord(
                        existing.mission_id,
                        existing.task_id,
                        existing.execution_id,
                        existing.task,
                        QueueState.FAILED.value,
                        f"runtime raised {type(exc).__name__}",
                        existing.created_at,
                        _now(),
                        item.attempts,
                    )
                )
            raise

        success = result.state is ExecutionState.VERIFIED
        if existing is not None:
            self.store.put(
                MissionRecord(
                    existing.mission_id,
                    existing.task_id,
                    existing.execution_id,
                    existing.task,
                    "verified" if success else result.state.value,
                    _bounded_text(result.reason, 1000),
                    existing.created_at,
                    _now(),
                    item.attempts,
                )
            )
        return success


__all__ = ["MissionController", "MissionRecord", "MissionStore"]
