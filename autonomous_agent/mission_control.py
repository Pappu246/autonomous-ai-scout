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
from .mission_orchestrator import MissionOrchestrator
from .task_dag import DAGTaskSpec
from .task_planner import default_grants_for_task
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
    completed_steps: tuple[str, ...] = ()

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
                    completed_steps=tuple(_bounded_text(value, 80) for value in item.get("completed_steps", ()) if str(value).strip()),
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

    def get_by_task_id(self, task_id: str) -> MissionRecord | None:
        wanted = task_id.strip()
        return next((item for item in self.list() if item.task_id == wanted), None)

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
        self.memory.record_episode(self.MEMORY_PROJECT, prepared.task, outcome="queued", metadata={"specialist_role": role.role.value})
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
        grants = []
        seen = set()
        for spec in specs:
            for capability in default_grants_for_task(spec.task, self.core._registry):
                if capability not in seen:
                    seen.add(capability)
                    grants.append(capability)
        prepared = self.core.prepare_dag(objective, specs, granted=tuple(grants))
        mission_id, execution_id = self._new_ids()
        now = _now()
        role = choose_specialist(objective)
        if not prepared.executable:
            return self.store.put(MissionRecord(
                mission_id, mission_id, execution_id, _bounded_text(objective, 4000), "blocked",
                _bounded_text(prepared.reason, 1000), now, now, 0, role.role.value, normalized,
            ))
        item = self.queue.enqueue(_bounded_text(objective, 4000), task_id=mission_id, execution_id=execution_id)
        self.memory.record_episode(self.MEMORY_PROJECT, objective, outcome="queued", metadata={"specialist_role": role.role.value, "kind": "dag"})
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
        self.memory.record_episode(self.MEMORY_PROJECT, record.task, outcome="cancelled", metadata={"specialist_role": record.specialist_role})
        return self.store.put(MissionRecord(
            record.mission_id, record.task_id, record.execution_id, record.task, item.state.value,
            "mission cancelled before execution", record.created_at, item.updated_at, item.attempts,
            record.specialist_role, record.steps,
        ))

    def run_once(self) -> MissionRecord | None:
        item = self.worker.run_once()
        return None if item is None else self.store.get_by_task_id(item.task_id)

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

    def resume(self, mission_id: str) -> MissionRecord:
        record = self.store.get(mission_id)
        if record is None:
            raise KeyError(mission_id)
        if record.state not in {
            QueueState.FAILED.value,
            QueueState.RECOVERY_REQUIRED.value,
            "blocked",
        }:
            raise ValueError(f"mission cannot be resumed from state {record.state}")
        task_id = f"{record.mission_id}-resume-{uuid.uuid4().hex[:12]}"
        execution_id = f"exec-{uuid.uuid4().hex}"
        item = self.queue.enqueue(
            record.task,
            task_id=task_id,
            execution_id=execution_id,
        )
        self.memory.record_episode(
            self.MEMORY_PROJECT,
            record.task,
            outcome="resume_queued",
            metadata={"specialist_role": record.specialist_role, "completed_steps": len(record.completed_steps)},
        )
        return self.store.put(MissionRecord(
            record.mission_id,
            item.task_id,
            item.execution_id,
            record.task,
            item.state.value,
            _bounded_text(
                f"mission resumed; {len(record.completed_steps)}/{len(record.steps) or 1} verified steps will be retained",
                1000,
            ),
            record.created_at,
            item.updated_at,
            item.attempts,
            record.specialist_role,
            record.steps,
            record.completed_steps,
        ))

    def _run_steps(self, item: QueueItem, existing: MissionRecord) -> tuple[bool, str]:
        specs = tuple(
            DAGTaskSpec(
                str(step["node_id"]),
                str(step["task"]),
                tuple(step["depends_on"]),
            )
            for step in existing.steps
        )
        grants = []
        seen = set()
        for spec in specs:
            for capability in default_grants_for_task(spec.task, self.core._registry):
                if capability not in seen:
                    seen.add(capability)
                    grants.append(capability)
        plan = self.core.prepare_dag(existing.task, specs, granted=tuple(grants))
        orchestrator = MissionOrchestrator()

        def runner(node):
            step_execution_id = f"{item.execution_id}:{node.node_id}"
            result = run_task(
                node.task,
                root=self.root,
                audit_path=self.audit_path,
                journal_path=self.journal_path,
                execution_id=step_execution_id,
                memory=self.memory.store,
                project=self.MEMORY_PROJECT,
            )
            if result.state is ExecutionState.VERIFIED:
                latest = self.store.get(existing.mission_id) or existing
                completed = tuple(sorted(set(latest.completed_steps) | {node.node_id}))
                self.store.put(MissionRecord(
                    latest.mission_id, latest.task_id, latest.execution_id, latest.task,
                    QueueState.RUNNING.value,
                    _bounded_text(
                        f"DAG progress {len(completed)}/{len(existing.steps)} steps verified",
                        1000,
                    ),
                    latest.created_at, _now(), item.attempts, latest.specialist_role,
                    latest.steps, completed,
                ))
            return result

        outcome = orchestrator.execute(
            plan,
            completed_steps=existing.completed_steps,
            runner=runner,
        )
        return outcome.success, _bounded_text(outcome.reason, 1000)

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
        runtime_state = QueueState.FAILED.value
        try:
            if existing and existing.steps:
                success, reason = self._run_steps(item, existing)
                runtime_state = "verified" if success else QueueState.FAILED.value
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
                runtime_state = "verified" if success else result.state.value
                reason = result.reason
        except Exception as exc:
            success = False
            runtime_state = QueueState.FAILED.value
            reason = f"runtime raised {type(exc).__name__}"
        outcome = runtime_state
        self.memory.record_episode(self.MEMORY_PROJECT, item.task, outcome=outcome, metadata={"specialist_role": role})
        latest = self.store.get_by_task_id(item.task_id) or existing
        if latest:
            self.store.put(MissionRecord(
                latest.mission_id, latest.task_id, latest.execution_id, latest.task,
                outcome, _bounded_text(reason, 1000),
                latest.created_at, _now(), item.attempts, role, latest.steps, latest.completed_steps,
            ))
        return success


__all__ = ["MissionController", "MissionRecord", "MissionStore"]
