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
from .persistent_memory import PersistentMemory
from .specialist_router import SpecialistRole, choose_specialist
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
    specialist_role: str = SpecialistRole.GENERAL.value

    @property
    def terminal(self) -> bool:
        return self.state in {
            "blocked",
            "verified",
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
                    specialist_role=str(item.get("specialist_role", SpecialistRole.GENERAL.value)),
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
    """Mission facade that composes the existing planner, queue, worker, memory and runtime."""

    MEMORY_PROJECT = "mission-control"

    def __init__(
        self,
        *,
        root: Path | None = None,
        queue_path: str | Path = "state/mission_queue.json",
        missions_path: str | Path = "state/missions.json",
        memory_path: str | Path = "state/mission_memory.json",
        audit_path: str | Path = "state/runtime_execution.jsonl",
        journal_path: str | Path = "state/runtime_runs.jsonl",
    ) -> None:
        self.root = (root or Path.cwd()).resolve()
        self.queue = TaskQueueStore(self.root / queue_path)
        self.store = MissionStore(self.root / missions_path)
        self.memory = PersistentMemory(self.root / memory_path)
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

    def _history_hint(self, task: str) -> str:
        matches = self.memory.recall(self.MEMORY_PROJECT, task, limit=3)
        if not matches:
            return "no matching prior mission memory"
        return f"recalled {len(matches)} prior mission memory item(s)"

    def submit(self, task: str) -> MissionRecord:
        prepared = self.core.prepare(task)
        mission_id = f"mission-{uuid.uuid4().hex}"
        execution_id = f"exec-{uuid.uuid4().hex}"
        now = _now()
        role = choose_specialist(prepared.task)
        history = self._history_hint(prepared.task)

        if not prepared.plan.executable:
            return self.store.put(
                MissionRecord(
                    mission_id=mission_id,
                    task_id=mission_id,
                    execution_id=execution_id,
                    task=prepared.task,
                    state="blocked",
                    reason=_bounded_text(
                        f"{prepared.plan.reason}; specialist={role.role.value}; memory={history}",
                        1000,
                    ),
                    created_at=now,
                    updated_at=now,
                    specialist_role=role.role.value,
                )
            )

        item = self.queue.enqueue(prepared.task, task_id=mission_id, execution_id=execution_id)
        self.memory.record_task(
            self.MEMORY_PROJECT,
            prepared.task,
            intent=role.role.value,
            outcome="queued",
        )
        return self.store.put(
            MissionRecord(
                mission_id=mission_id,
                task_id=item.task_id,
                execution_id=item.execution_id,
                task=item.task,
                state=item.state.value,
                reason=_bounded_text(
                    f"mission accepted; specialist={role.role.value}; confidence={role.confidence}; memory={history}",
                    1000,
                ),
                created_at=item.created_at,
                updated_at=item.updated_at,
                attempts=item.attempts,
                specialist_role=role.role.value,
            )
        )

    def cancel(self, mission_id: str) -> MissionRecord:
        record = self.store.get(mission_id)
        if record is None:
            raise KeyError(mission_id)
        item = self.queue.cancel(record.task_id)
        self.memory.record_task(
            self.MEMORY_PROJECT,
            record.task,
            intent=record.specialist_role,
            outcome="cancelled",
        )
        return self.store.put(
            MissionRecord(
                record.mission_id, record.task_id, record.execution_id, record.task,
                item.state.value, "mission cancelled before execution",
                record.created_at, item.updated_at, item.attempts, record.specialist_role,
            )
        )

    def run_once(self) -> MissionRecord | None:
        item = self.worker.run_once()
        return None if item is None else self.store.get(item.task_id)

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
                        existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                        item.state.value, _bounded_text(item.last_error, 1000),
                        existing.created_at, item.updated_at, item.attempts, existing.specialist_role,
                    )
                )
            )
        return tuple(recovered)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.recover()
        self._stop.clear()
        self._thread = threading.Thread(target=self.worker.run_forever, args=(self._stop,), name="autonomous-scout-mission-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def _handle_queue_item(self, item: QueueItem) -> bool:
        existing = self.store.get(item.task_id)
        role = existing.specialist_role if existing else choose_specialist(item.task).role.value
        if existing is not None:
            self.store.put(
                MissionRecord(
                    existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                    QueueState.RUNNING.value,
                    _bounded_text(f"mission executing through canonical runtime; specialist={role}", 1000),
                    existing.created_at, _now(), item.attempts, role,
                )
            )

        try:
            result = run_task(
                item.task,
                root=self.root,
                audit_path=self.audit_path,
                journal_path=self.journal_path,
                execution_id=item.execution_id,
                memory=self.memory.store,
                project=self.MEMORY_PROJECT,
            )
        except Exception as exc:
            self.memory.record_task(self.MEMORY_PROJECT, item.task, intent=role, outcome=f"runtime_exception:{type(exc).__name__}")
            if existing is not None:
                self.store.put(
                    MissionRecord(
                        existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                        QueueState.FAILED.value, f"runtime raised {type(exc).__name__}",
                        existing.created_at, _now(), item.attempts, role,
                    )
                )
            raise

        success = result.state is ExecutionState.VERIFIED
        outcome = "verified" if success else result.state.value
        self.memory.record_task(self.MEMORY_PROJECT, item.task, intent=role, outcome=outcome)
        if existing is not None:
            self.store.put(
                MissionRecord(
                    existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                    outcome, _bounded_text(result.reason, 1000),
                    existing.created_at, _now(), item.attempts, role,
                )
            )
        return success


__all__ = ["MissionController", "MissionRecord", "MissionStore"]
