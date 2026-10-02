from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .background_worker import BackgroundTaskWorker
from .execution_engine import ExecutionState
from .file_lock import InterProcessFileLock
from .persistent_memory import PersistentMemory
from .specialist_router import SpecialistRole, choose_specialist
from .task_core import AutonomousTaskCore
from .task_dag import DAGTaskSpec
from .task_queue import QueueItem, QueueState, TaskQueueStore
from .runtime import run_task


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _safe_steps(value: object) -> tuple[dict[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > 16:
        raise ValueError("mission steps must be a bounded list of at most 16 items")
    safe: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("each mission step must be an object")
        node_id = _bounded_text(raw.get("node_id", ""), 80)
        task = _bounded_text(raw.get("task", ""), 4000)
        depends_on = raw.get("depends_on", ())
        if not node_id or not task:
            raise ValueError("mission step requires node_id and task")
        if not isinstance(depends_on, (list, tuple)):
            raise ValueError("depends_on must be a list")
        safe.append(
            {
                "node_id": node_id,
                "task": task,
                "depends_on": tuple(_bounded_text(item, 80) for item in depends_on),
            }
        )
    return tuple(safe)


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
    steps: tuple[dict[str, object], ...] = ()

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
                    steps=_safe_steps(item.get("steps", ())),
                )
            )
        return records

    def _save_unlocked(self, records: Iterable[MissionRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
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
            updated = [record if item.mission_id == record.mission_id else item for item in records]
            if not any(item.mission_id == record.mission_id for item in records):
                updated.append(record)
            self._save_unlocked(updated)
        return record


class MissionController:
    """Mission facade composing planner, bounded DAG, queue, worker, memory and runtime."""

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
        self.worker = BackgroundTaskWorker(self.queue, self._handle_queue_item, poll_interval=0.25)

    def _history_hint(self, task: str) -> str:
        matches = self.memory.recall(self.MEMORY_PROJECT, task, limit=3)
        return "no matching prior mission memory" if not matches else f"recalled {len(matches)} prior mission memory item(s)"

    def _new_ids(self) -> tuple[str, str]:
        return f"mission-{uuid.uuid4().hex}", f"exec-{uuid.uuid4().hex}"

    def submit(self, task: str) -> MissionRecord:
        prepared = self.core.prepare(task)
        mission_id, execution_id = self._new_ids()
        now = _now()
        role = choose_specialist(prepared.task)
        history = self._history_hint(prepared.task)
        if not prepared.plan.executable:
            return self.store.put(MissionRecord(
                mission_id, mission_id, execution_id, prepared.task, "blocked",
                _bounded_text(f"{prepared.plan.reason}; specialist={role.role.value}; memory={history}", 1000),
                now, now, 0, role.role.value, (),
            ))
        item = self.queue.enqueue(prepared.task, task_id=mission_id, execution_id=execution_id)
        self.memory.record_task(self.MEMORY_PROJECT, prepared.task, intent=role.role.value, outcome="queued")
        return self.store.put(MissionRecord(
            mission_id, item.task_id, item.execution_id, item.task, item.state.value,
            _bounded_text(f"mission accepted; specialist={role.role.value}; confidence={role.confidence}; memory={history}", 1000),
            item.created_at, item.updated_at, item.attempts, role.role.value, (),
        ))

    def submit_plan(self, objective: str, steps: Sequence[Mapping[str, object]]) -> MissionRecord:
        normalized = _safe_steps(steps)
        specs = tuple(
            DAGTaskSpec(
                str(step["node_id"]),
                str(step["task"]),
                tuple(step["depends_on"]),
            )
            for step in normalized
        )
        prepared = self.core.prepare_dag(objective, specs)
        mission_id, execution_id = self._new_ids()
        now = _now()
        role = choose_specialist(objective)
        if not prepared.executable:
            return self.store.put(MissionRecord(
                mission_id, mission_id, execution_id, _bounded_text(objective, 4000), "blocked",
                _bounded_text(prepared.reason, 1000), now, now, 0, role.role.value, normalized,
            ))
        item = self.queue.enqueue(_bounded_text(objective, 4000), task_id=mission_id, execution_id=execution_id)
        self.memory.record_task(self.MEMORY_PROJECT, objective, intent=f"dag:{role.role.value}", outcome="queued")
        return self.store.put(MissionRecord(
            mission_id, item.task_id, item.execution_id, item.task, item.state.value,
            _bounded_text(f"bounded mission DAG accepted: {len(normalized)} steps; specialist={role.role.value}", 1000),
            item.created_at, item.updated_at, item.attempts, role.role.value, normalized,
        ))

    def cancel(self, mission_id: str) -> MissionRecord:
        record = self.store.get(mission_id)
        if record is None:
            raise KeyError(mission_id)
        item = self.queue.cancel(record.task_id)
        self.memory.record_task(self.MEMORY_PROJECT, record.task, intent=record.specialist_role, outcome="cancelled")
        return self.store.put(MissionRecord(
            record.mission_id, record.task_id, record.execution_id, record.task, item.state.value,
            "mission cancelled before execution", record.created_at, item.updated_at, item.attempts,
            record.specialist_role, record.steps,
        ))

    def run_once(self) -> MissionRecord | None:
        item = self.worker.run_once()
        return None if item is None else self.store.get(item.task_id)

    def recover(self) -> tuple[MissionRecord, ...]:
        items = self.worker.recover()
        recovered: list[MissionRecord] = []
        for item in items:
            existing = self.store.get(item.task_id)
            if existing:
                recovered.append(self.store.put(MissionRecord(
                    existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                    item.state.value, _bounded_text(item.last_error, 1000),
                    existing.created_at, item.updated_at, item.attempts,
                    existing.specialist_role, existing.steps,
                )))
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

    def _run_steps(self, item: QueueItem, existing: MissionRecord) -> tuple[bool, str]:
        by_id = {str(step["node_id"]): step for step in existing.steps}
        remaining = set(by_id)
        completed: set[str] = set()
        while remaining:
            ready = sorted(
                node_id for node_id in remaining
                if set(by_id[node_id]["depends_on"]).issubset(completed)
            )
            if not ready:
                return False, "mission DAG reached a dependency deadlock"
            for node_id in ready:
                step = by_id[node_id]
                step_task = str(step["task"])
                step_execution_id = f"{item.execution_id}:{node_id}"
                result = run_task(
                    step_task,
                    root=self.root,
                    audit_path=self.audit_path,
                    journal_path=self.journal_path,
                    execution_id=step_execution_id,
                    memory=self.memory.store,
                    project=self.MEMORY_PROJECT,
                )
                if result.state is not ExecutionState.VERIFIED:
                    return False, _bounded_text(f"step {node_id} {result.state.value}: {result.reason}", 1000)
                completed.add(node_id)
                remaining.remove(node_id)
            self.store.put(MissionRecord(
                existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                QueueState.RUNNING.value,
                _bounded_text(f"DAG progress {len(completed)}/{len(by_id)} steps verified", 1000),
                existing.created_at, _now(), item.attempts, existing.specialist_role, existing.steps,
            ))
        return True, _bounded_text(f"all {len(completed)} mission steps verified", 1000)

    def _handle_queue_item(self, item: QueueItem) -> bool:
        existing = self.store.get(item.task_id)
        role = existing.specialist_role if existing else choose_specialist(item.task).role.value
        if existing:
            self.store.put(MissionRecord(
                existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                QueueState.RUNNING.value,
                _bounded_text(f"mission executing; specialist={role}", 1000),
                existing.created_at, _now(), item.attempts, role, existing.steps,
            ))
        try:
            if existing and existing.steps:
                success, reason = self._run_steps(item, existing)
            else:
                result = run_task(
                    item.task,
                    root=self.root,
                    audit_path=self.audit_path,
                    journal_path=self.journal_path,
                    execution_id=item.execution_id,
                    memory=self.memory.store,
                    project=self.MEMORY_PROJECT,
                )
                success = result.state is ExecutionState.VERIFIED
                reason = result.reason
        except Exception as exc:
            success = False
            reason = f"runtime raised {type(exc).__name__}"
        outcome = "verified" if success else QueueState.FAILED.value
        self.memory.record_task(self.MEMORY_PROJECT, item.task, intent=role, outcome=outcome)
        if existing:
            self.store.put(MissionRecord(
                existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                outcome, _bounded_text(reason, 1000),
                existing.created_at, _now(), item.attempts, role, existing.steps,
            ))
        return success


__all__ = ["MissionController", "MissionRecord", "MissionStore"]
