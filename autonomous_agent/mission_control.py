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

from .action_queue import build_action_proposal, enqueue_proposal, load_queue
from .approval_store import load_approval, reject_action
from .approved_executor import claim_approval, validate_approval
from .capability_policy import Capability
from .background_worker import BackgroundTaskWorker
from .computer.connector import BoundedComputerConnector
from .execution_audit import append_execution_record
from .execution_engine import ExecutionState
from .file_lock import InterProcessFileLock
from .persistent_memory import PersistentMemory
from .specialist_policy import specialist_grants
from .specialist_provider import SpecialistProviderRouter
from .specialist_router import SpecialistRole, choose_specialist
from .provider_router import providers_from_env
from .task_core import AutonomousTaskCore
from .mission_orchestrator import MissionOrchestrator
from .tool_registry import ApprovalRequirement
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
    approval_action_id: str = ""

    @property
    def terminal(self) -> bool:
        return self.state in {
            "blocked",
            "verified",
            QueueState.SUCCEEDED.value,
            QueueState.FAILED.value,
            QueueState.CANCELLED.value,
            "rejected",
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
                    approval_action_id=str(item.get("approval_action_id", "")),
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

    def get_by_approval_action_id(self, action_id: str) -> MissionRecord | None:
        wanted = action_id.strip()
        return next((item for item in self.list() if item.approval_action_id == wanted), None)

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
        computer_connector: BoundedComputerConnector | None = None,
    ) -> None:
        self.root = (root or Path.cwd()).resolve()
        self.queue = TaskQueueStore(self.root / queue_path)
        self.store = MissionStore(self.root / missions_path)
        self.memory = PersistentMemory(self.root / memory_path)
        self.audit_path = self.root / audit_path
        self.journal_path = self.root / journal_path
        self.approval_queue_path = self.root / "state" / "approval_queue.json"
        self.approval_dir = self.root / "state" / "approvals"
        self.approval_audit_path = self.root / "state" / "approval_audit.jsonl"
        self.approval_claim_dir = self.root / "state" / "approval_claims"
        self.computer_connector = computer_connector or BoundedComputerConnector()
        self.provider_router = SpecialistProviderRouter(providers_from_env())
        self.core = AutonomousTaskCore()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.worker = BackgroundTaskWorker(self.queue, self._handle_queue_item, poll_interval=0.25)

    def _history_hint(self, task: str) -> str:
        matches = self.memory.recall(self.MEMORY_PROJECT, task, limit=3)
        return "no matching prior mission memory" if not matches else f"recalled {len(matches)} prior mission memory item(s)"

    def _new_ids(self) -> tuple[str, str]:
        return f"mission-{uuid.uuid4().hex}", f"exec-{uuid.uuid4().hex}"


    def _provider_route_hint(self, role: SpecialistRole | str) -> str:
        resolved = role.value if isinstance(role, SpecialistRole) else str(role)
        route = self.provider_router.route(resolved)
        if not route.eligible or route.provider is None:
            return "provider=unconfigured"
        return f"provider={route.provider.name}"

    def _record_provider_route(self, execution_id: str, role: SpecialistRole | str) -> None:
        resolved = role.value if isinstance(role, SpecialistRole) else str(role)
        route = self.provider_router.route(resolved)
        append_execution_record(
            self.audit_path,
            {
                "execution_id": execution_id,
                "timestamp": _now(),
                "state": ExecutionState.RUNNING.value,
                "event": "specialist_provider_route",
                "specialist_role": route.role,
                "provider": "" if route.provider is None else route.provider.name,
                "eligible": route.eligible,
                "selection_only": True,
                "reason": _bounded_text(route.reason, 500),
            },
        )

    def _record_computer_evidence(self, execution_id: str, result) -> None:
        for item in getattr(result, "results", ()):
            command = getattr(item, "command", ())
            if not isinstance(command, (tuple, list)) or "COMPUTER" not in {str(value).upper() for value in command}:
                continue
            raw_output = getattr(item, "output", "")
            if not isinstance(raw_output, str) or not raw_output.strip():
                continue
            try:
                payload = json.loads(raw_output)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            evidence = {
                key: payload[key]
                for key in ("state", "verified", "turns", "actions", "reason")
                if key in payload
            }
            if not evidence:
                continue
            append_execution_record(
                self.audit_path,
                {
                    "execution_id": execution_id,
                    "timestamp": _now(),
                    "state": ExecutionState.RUNNING.value,
                    "event": "computer_evidence",
                    "evidence_json": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                },
            )

    @staticmethod
    def _approval_required(reason: str) -> bool:
        text = str(reason).lower()
        return "explicit approval" in text or "requires explicit approval" in text or "human review" in text

    @staticmethod
    def _computer_request(task: str) -> dict[str, object]:
        return {
            "computer.use": {"task": task, "max_turns": 20},
            "computer.use:credref": None,
        }

    @staticmethod
    def _result_summary(result) -> str:
        """Expose bounded, non-sensitive result summaries without copying raw tool output into mission state."""
        summaries: list[str] = []
        for item in getattr(result, "results", ()):
            operation = str(getattr(item, "operation", "unknown"))
            output = str(getattr(item, "output", "") or "")
            verification = str(getattr(item, "verification_status", "") or "")
            if operation == "test":
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                relevant = [
                    line for line in lines
                    if any(marker in line.lower() for marker in ("passed", "failed", "error", "skipped", "network isolation unavailable"))
                ]
                detail = relevant[-1] if relevant else verification or "no test summary emitted"
            elif operation == "lint":
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                detail = lines[-1] if lines else verification or "no lint summary emitted"
            elif operation == "inspect":
                file_lines = output.splitlines()
                detail = f"listed_files={max(0, len(file_lines) - 1)}"
                if "truncated" in output.lower():
                    detail += "; output_truncated"
            else:
                detail = verification or ("success" if getattr(item, "success", False) else "failed")
            summaries.append(f"{operation}={detail[:240]}")
        return "; ".join(summaries[:8])

    def _queue_approval(self, task: str, steps: Iterable[str], *, risk: str = "high") -> str:
        proposal = build_action_proposal(
            task,
            tuple(_bounded_text(step, 2048) for step in steps),
            llm_requires_approval=True,
        )
        action = enqueue_proposal(self.approval_queue_path, proposal, risk=risk)
        return "" if action is None else action.id

    def _plan_requires_approval(self, plan) -> bool:
        for step in plan.steps:
            tool = self.core._registry.get(step.tool_name)
            if tool is None:
                continue
            if tool.approval_requirement is not ApprovalRequirement.NONE or not tool.safe_autonomous:
                return True
        return False

    def submit(self, task: str) -> MissionRecord:
        normalized_task = " ".join(str(task).split())
        if not normalized_task:
            raise ValueError("mission task is required")
        role_decision = choose_specialist(normalized_task)
        role = role_decision.role
        grants = specialist_grants(normalized_task, role)
        prepared = self.core.prepare(normalized_task, granted=grants)
        approval_grants = specialist_grants(normalized_task, role, include_approval_tools=True)
        approval_preview = self.core.prepare(
            normalized_task,
            granted=approval_grants,
            explicitly_approved=True,
        )
        mission_id, execution_id = self._new_ids()
        now = _now()
        history = self._history_hint(prepared.task)
        provider_hint = self._provider_route_hint(role)
        if not prepared.plan.executable:
            if approval_preview.plan.executable and self._plan_requires_approval(approval_preview.plan):
                approval_id = self._queue_approval(
                    prepared.task,
                    (step.description for step in prepared.plan.steps),
                    risk=prepared.plan.risk.value,
                )
                return self.store.put(MissionRecord(
                    mission_id, mission_id, execution_id, prepared.task, "requires_approval",
                    _bounded_text(
                        f"{prepared.plan.reason}; approval_action={approval_id}; specialist={role.value}; {provider_hint}; memory={history}",
                        1000,
                    ),
                    now, now, 0, role.value, (), (), approval_id,
                ))
            return self.store.put(MissionRecord(
                mission_id, mission_id, execution_id, prepared.task, "blocked",
                _bounded_text(f"{prepared.plan.reason}; specialist={role.value}; memory={history}", 1000),
                now, now, 0, role.value, (),
            ))
        item = self.queue.enqueue(prepared.task, task_id=mission_id, execution_id=execution_id)
        self.memory.record_episode(
            self.MEMORY_PROJECT,
            prepared.task,
            outcome="queued",
            metadata={"specialist_role": role.value, "provider_hint": provider_hint},
        )
        return self.store.put(MissionRecord(
            mission_id, item.task_id, item.execution_id, item.task, item.state.value,
            _bounded_text(
                f"mission accepted; specialist={role.value}; confidence={role_decision.confidence}; {provider_hint}; memory={history}",
                1000,
            ),
            item.created_at, item.updated_at, item.attempts, role.value, (),
        ))

    def submit_plan(self, objective: str, steps: Sequence[Mapping[str, object]]) -> MissionRecord:
        normalized_objective = _bounded_text(objective, 4000)
        if not normalized_objective:
            raise ValueError("mission objective is required")
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
            step_grants = specialist_grants(spec.task)
            for capability in step_grants:
                if capability not in seen:
                    seen.add(capability)
                    grants.append(capability)
        prepared = self.core.prepare_dag(normalized_objective, specs, granted=tuple(grants))
        approval_grants: list[Capability] = []
        approval_seen: set[Capability] = set()
        for spec in specs:
            for capability in specialist_grants(
                spec.task,
                include_approval_tools=True,
            ):
                if capability not in approval_seen:
                    approval_seen.add(capability)
                    approval_grants.append(capability)
        approval_preview = self.core.prepare_dag(
            normalized_objective,
            specs,
            granted=tuple(approval_grants),
            explicitly_approved=True,
        )
        mission_id, execution_id = self._new_ids()
        now = _now()
        role = choose_specialist(normalized_objective)
        provider_hint = self._provider_route_hint(role.role)
        if not prepared.executable:
            if approval_preview.executable and any(
                self._plan_requires_approval(node.plan) for node in approval_preview.nodes
            ):
                approval_id = self._queue_approval(
                    normalized_objective,
                    (str(step.get("task", "")) for step in normalized),
                    risk="high",
                )
                return self.store.put(MissionRecord(
                    mission_id, mission_id, execution_id, _bounded_text(objective, 4000), "requires_approval",
                    _bounded_text(
                        f"{prepared.reason}; approval_action={approval_id}; {provider_hint}",
                        1000,
                    ),
                    now, now, 0, role.role.value, normalized, (), approval_id,
                ))
            return self.store.put(MissionRecord(
                mission_id, mission_id, execution_id, _bounded_text(objective, 4000), "blocked",
                _bounded_text(prepared.reason, 1000), now, now, 0, role.role.value, normalized,
            ))
        item = self.queue.enqueue(_bounded_text(objective, 4000), task_id=mission_id, execution_id=execution_id)
        self.memory.record_episode(
            self.MEMORY_PROJECT,
            normalized_objective,
            outcome="queued",
            metadata={"specialist_role": role.role.value, "provider_hint": provider_hint, "kind": "dag"},
        )
        return self.store.put(MissionRecord(
            mission_id, item.task_id, item.execution_id, item.task, item.state.value,
            _bounded_text(f"bounded mission DAG accepted: {len(normalized)} steps; specialist={role.role.value}; {provider_hint}", 1000),
            item.created_at, item.updated_at, item.attempts, role.role.value, normalized,
        ))

    def timeline(self, mission_id: str) -> tuple[dict[str, object], ...]:
        record = self.store.get(mission_id)
        if record is None:
            raise KeyError(mission_id)

        execution_ids = {record.execution_id}
        for step in record.steps:
            execution_ids.add(f"{record.execution_id}:{step.get('node_id', '')}")

        events: list[dict[str, object]] = []
        if self.audit_path.exists():
            try:
                lines = self.audit_path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                raise ValueError("mission audit is unreadable") from exc
            for line in lines:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                execution_id = str(item.get("execution_id", ""))
                if execution_id not in execution_ids:
                    continue
                safe: dict[str, object] = {}
                for key in (
                    "timestamp",
                    "state",
                    "event",
                    "tool",
                    "step_id",
                    "verification",
                    "result",
                    "reason",
                    "attempt",
                    "completed_steps",
                    "evidence_json",
                    "specialist_role",
                    "provider",
                    "eligible",
                    "selection_only",
                ):
                    if key in item:
                        value = item[key]
                        if isinstance(value, (str, int, float, bool)) or value is None:
                            safe[key] = value
                        elif isinstance(value, (list, tuple)):
                            safe[key] = list(value)[:32]
                safe["execution_id"] = execution_id
                events.append(safe)

        if record.approval_action_id:
            for action in load_queue(self.approval_queue_path):
                if action.id == record.approval_action_id:
                    events.append(
                        {
                            "event": "approval",
                            "timestamp": action.created_at,
                            "approval_action_id": action.id,
                            "approval_status": action.status,
                            "reason": action.reason,
                        }
                    )
                    break

        events.append(
            {
                "event": "mission_state",
                "timestamp": record.updated_at,
                "mission_id": record.mission_id,
                "state": record.state,
                "reason": record.reason,
                "specialist_role": record.specialist_role,
                "completed_steps": list(record.completed_steps),
            }
        )
        events.sort(key=lambda item: (str(item.get("timestamp", "")), str(item.get("event", ""))))
        return tuple(events)

    def cancel(self, mission_id: str) -> MissionRecord:
        record = self.store.get(mission_id)
        if record is None:
            raise KeyError(mission_id)
        if record.state == "requires_approval":
            if record.approval_action_id:
                queue = {item.id: item for item in load_queue(self.approval_queue_path)}
                action = queue.get(record.approval_action_id)
                if action is not None and action.status == "pending":
                    reject_action(
                        self.approval_queue_path,
                        record.approval_action_id,
                        audit_path=self.approval_audit_path,
                    )
            self.memory.record_episode(
                self.MEMORY_PROJECT,
                record.task,
                outcome="cancelled",
                metadata={"specialist_role": record.specialist_role},
            )
            return self.store.put(MissionRecord(
                record.mission_id,
                record.task_id,
                record.execution_id,
                record.task,
                "cancelled",
                "mission cancelled before approval/activation",
                record.created_at,
                _now(),
                record.attempts,
                record.specialist_role,
                record.steps,
                record.completed_steps,
                record.approval_action_id,
            ))
        item = self.queue.cancel(record.task_id)
        self.memory.record_episode(self.MEMORY_PROJECT, record.task, outcome="cancelled", metadata={"specialist_role": record.specialist_role})
        return self.store.put(MissionRecord(
            record.mission_id, record.task_id, record.execution_id, record.task, item.state.value,
            "mission cancelled before execution", record.created_at, item.updated_at, item.attempts,
            record.specialist_role, record.steps, record.completed_steps, record.approval_action_id,
        ))

    def run_once(self) -> MissionRecord | None:
        item = self.worker.run_once()
        return None if item is None else self.store.get_by_task_id(item.task_id)

    def recover(self) -> tuple[MissionRecord, ...]:
        items = self.worker.recover()
        recovered: list[MissionRecord] = []
        for item in items:
            existing = self.store.get_by_task_id(item.task_id)
            if existing:
                recovered.append(self.store.put(MissionRecord(
                    existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                    item.state.value, _bounded_text(item.last_error, 1000),
                    existing.created_at, item.updated_at, item.attempts,
                    existing.specialist_role, existing.steps, existing.completed_steps,
                    existing.approval_action_id,
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

    def _validate_pending_approval(self, action_id: str):
        queue = {item.id: item for item in load_queue(self.approval_queue_path)}
        action = queue.get(action_id)
        if action is None:
            raise KeyError(action_id)
        if action.status != "approved":
            raise ValueError("approval action must be explicitly approved before mission activation")
        approval = load_approval(self.approval_dir, action_id)
        decision = validate_approval(action, approval, audit_path=self.approval_audit_path)
        if not decision.allowed:
            raise ValueError(decision.reason)
        return action, approval

    def activate_approved_action(self, action_id: str) -> MissionRecord | None:
        action, _approval = self._validate_pending_approval(action_id)
        record = self.store.get_by_approval_action_id(action_id)
        if record is None:
            return None
        if record.state != "requires_approval":
            return record
        task_id, execution_id = self._new_ids()
        item = self.queue.enqueue(record.task, task_id=task_id, execution_id=execution_id)
        self.memory.record_episode(
            self.MEMORY_PROJECT,
            record.task,
            outcome="approval_accepted",
            metadata={"specialist_role": record.specialist_role, "approval_action_id": action_id},
        )
        return self.store.put(MissionRecord(
            record.mission_id,
            item.task_id,
            item.execution_id,
            record.task,
            item.state.value,
            _bounded_text(f"approval accepted; mission queued; approval_action={action_id}", 1000),
            record.created_at,
            item.updated_at,
            item.attempts,
            record.specialist_role,
            record.steps,
            record.completed_steps,
            action_id,
        ))

    def reject_approved_action(self, action_id: str) -> MissionRecord | None:
        record = self.store.get_by_approval_action_id(action_id)
        if record is None:
            return None
        if record.state != "requires_approval":
            return record
        self.memory.record_episode(
            self.MEMORY_PROJECT,
            record.task,
            outcome="approval_rejected",
            metadata={"specialist_role": record.specialist_role, "approval_action_id": action_id},
        )
        return self.store.put(MissionRecord(
            record.mission_id,
            record.task_id,
            record.execution_id,
            record.task,
            "rejected",
            _bounded_text(f"approval rejected; approval_action={action_id}", 1000),
            record.created_at,
            _now(),
            record.attempts,
            record.specialist_role,
            record.steps,
            record.completed_steps,
            action_id,
        ))

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
        if record.approval_action_id:
            approval_id = self._queue_approval(
                f"Resume mission {record.mission_id}: {record.task}",
                (str(step.get("task", "")) for step in record.steps),
                risk="high",
            )
            return self.store.put(MissionRecord(
                record.mission_id,
                record.task_id,
                record.execution_id,
                record.task,
                "requires_approval",
                _bounded_text(
                    f"resume requires fresh approval; approval_action={approval_id}",
                    1000,
                ),
                record.created_at,
                _now(),
                record.attempts,
                record.specialist_role,
                record.steps,
                record.completed_steps,
                approval_id,
            ))
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
            "",
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
        approved = bool(existing.approval_action_id)
        for spec in specs:
            for capability in specialist_grants(
                spec.task,
                existing.specialist_role,
                include_approval_tools=approved,
            ):
                if capability not in seen:
                    seen.add(capability)
                    grants.append(capability)
        plan = self.core.prepare_dag(
            existing.task,
            specs,
            granted=tuple(grants),
            explicitly_approved=approved,
        )
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
                granted=specialist_grants(
                    node.task,
                    existing.specialist_role,
                    include_approval_tools=approved,
                ),
                explicitly_approved=approved,
                computer_connector=self.computer_connector,
                computer_request=self._computer_request(node.task),
            )
            self._record_computer_evidence(step_execution_id, result)
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
                    latest.steps, completed, latest.approval_action_id,
                ))
            return result

        outcome = orchestrator.execute(
            plan,
            completed_steps=existing.completed_steps,
            runner=runner,
        )
        return outcome.success, _bounded_text(outcome.reason, 1000)

    def _handle_queue_item(self, item: QueueItem) -> bool:
        existing = self.store.get_by_task_id(item.task_id)
        role = existing.specialist_role if existing else choose_specialist(item.task).role.value
        if existing:
            self._record_provider_route(item.execution_id, role)
            self.store.put(MissionRecord(
                existing.mission_id, existing.task_id, existing.execution_id, existing.task,
                QueueState.RUNNING.value,
                _bounded_text(f"mission executing; specialist={role}; {self._provider_route_hint(role)}", 1000),
                existing.created_at, _now(), item.attempts, role, existing.steps, existing.completed_steps,
                existing.approval_action_id,
            ))
        runtime_state = QueueState.FAILED.value
        approval_validated = False
        try:
            if existing and existing.approval_action_id:
                _action, approval = self._validate_pending_approval(existing.approval_action_id)
                claim = claim_approval(approval, self.approval_claim_dir)
                if not claim.allowed:
                    raise ValueError(claim.reason)
                approval_validated = True
            if existing and existing.steps:
                success, reason = self._run_steps(item, existing)
                runtime_state = "verified" if success else QueueState.FAILED.value
            else:
                approved = approval_validated
                result = run_task(
                    item.task,
                    root=self.root,
                    audit_path=self.audit_path,
                    journal_path=self.journal_path,
                    execution_id=item.execution_id,
                    memory=self.memory.store,
                    project=self.MEMORY_PROJECT,
                    granted=specialist_grants(
                        item.task,
                        role,
                        include_approval_tools=approved,
                    ),
                    explicitly_approved=approved,
                    computer_connector=self.computer_connector,
                    computer_request=self._computer_request(item.task),
                )
                self._record_computer_evidence(item.execution_id, result)
                success = result.state is ExecutionState.VERIFIED
                runtime_state = "verified" if success else result.state.value
                summary = self._result_summary(result)
                reason = _bounded_text(
                    result.reason if not summary else f"{result.reason}; {summary}",
                    1000,
                )
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
                latest.approval_action_id,
            ))
        return success


__all__ = ["MissionController", "MissionRecord", "MissionStore"]
