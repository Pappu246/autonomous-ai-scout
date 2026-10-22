"""Hosted Mission Control facade over the fenced PostgreSQL coordinator.

This Preview pilot intentionally supports autonomous local read-only missions only.
It never instantiates or falls back to the legacy JSON-backed MissionController.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .postgres_coordinator import CoordinatorUnavailable, PostgresMissionCoordinator
from .postgres_mission_runner import PostgresMissionRunner
from .run_journal import _safe_text
from .vercel_state import hosted_runtime


@dataclass(frozen=True)
class HostedMissionRecord:
    mission_id: str
    task_id: str
    execution_id: str
    task: str
    state: str
    reason: str
    created_at: str
    updated_at: str
    attempts: int = 0
    specialist_role: str = "POSTGRES_READ_ONLY"
    steps: tuple[dict[str, object], ...] = ()
    completed_steps: tuple[str, ...] = ()
    approval_action_id: str = ""
    output: str = ""


class _PostgresMissionStore:
    def __init__(self, owner: "PostgresHostedMissionController") -> None:
        self.owner = owner

    def list(self) -> tuple[HostedMissionRecord, ...]:
        return tuple(self.owner._record(row) for row in self.owner.coordinator.list_missions())

    def get(self, mission_id: str) -> HostedMissionRecord | None:
        row = self.owner.coordinator.mission(mission_id)
        return None if row is None else self.owner._record(row, mission_id=mission_id)


class PostgresHostedMissionController:
    """Explicit hosted facade; all mission state reads/writes go through PostgreSQL."""

    uses_atomic_state_backend = True
    MEMORY_PROJECT = "mission-control"

    def __init__(self, *, dsn: str, namespace: str, root: str | Path) -> None:
        if not hosted_runtime():
            raise CoordinatorUnavailable("hosted PostgreSQL controller requires a hosted runtime")
        if not dsn or dsn != os.getenv("SCOUT_STATE_DATABASE_URL", "").strip():
            raise CoordinatorUnavailable("hosted PostgreSQL connection does not match configured secret")
        if namespace != os.getenv("SCOUT_STATE_NAMESPACE", "").strip():
            raise CoordinatorUnavailable("hosted PostgreSQL namespace is not configured")
        if os.getenv("SCOUT_STATE_BACKEND", "").strip().lower() != "postgres":
            raise CoordinatorUnavailable("hosted PostgreSQL backend is not explicitly selected")
        self.root = Path(root).resolve()
        self.state_backend_ready = False
        self.state_backend_error = "PostgreSQL coordinator readiness has not been verified"
        self.coordinator = PostgresMissionCoordinator(dsn, namespace)
        self.coordinator.check_ready()
        self.runner = PostgresMissionRunner(self.coordinator, root=self.root, allow_hosted=True)
        self.store = _PostgresMissionStore(self)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state_backend_ready = True
        self.state_backend_error = ""

    def _record(self, row: dict[str, object], *, mission_id: str | None = None) -> HostedMissionRecord:
        payload = row.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        raw_steps = payload.get("steps", [])
        if not isinstance(raw_steps, list):
            raw_steps = []
        steps = tuple(
            {key: value for key, value in item.items() if key in {"node_id", "task", "depends_on"}}
            for item in raw_steps if isinstance(item, dict)
        )[:64]
        completed = payload.get("completed_steps", [])
        if not isinstance(completed, list):
            completed = []
        return HostedMissionRecord(
            mission_id=str(mission_id or row.get("mission_id") or ""),
            task_id=str(row.get("task_id") or ""),
            execution_id=str(row.get("execution_id") or ""),
            task=_safe_text(payload.get("task", ""), 4000),
            state=str(row.get("state") or "unknown"),
            reason=_safe_text(row.get("reason", ""), 1000),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            attempts=max(0, int(row.get("attempts", 0) or 0)),
            specialist_role="POSTGRES_READ_ONLY",
            steps=steps,
            completed_steps=tuple(_safe_text(v, 80) for v in completed if isinstance(v, str))[:256],
            approval_action_id=_safe_text(payload.get("approval_action_id", ""), 128),
            output=_safe_text(payload.get("output", ""), 8192),
        )

    def submit(self, task: str, *, idempotency_key: str) -> HostedMissionRecord:
        if not self.state_backend_ready:
            raise CoordinatorUnavailable("PostgreSQL coordinator is not ready")
        submission = self.runner.submit(task, idempotency_key=idempotency_key)
        record = self.store.get(submission.mission_id)
        if record is None:
            raise CoordinatorUnavailable("submitted mission could not be read back; reconcile before retry")
        return record

    def submit_plan(self, objective: str, steps: Iterable[dict[str, object]]):
        raise ValueError("hosted PostgreSQL Preview currently accepts only validated local read-only missions")

    def cancel(self, mission_id: str) -> HostedMissionRecord:
        self.coordinator.cancel_pending(mission_id)
        record = self.store.get(mission_id)
        if record is None:
            raise KeyError(mission_id)
        return record

    def resume(self, mission_id: str) -> HostedMissionRecord:
        if self.store.get(mission_id) is None:
            raise KeyError(mission_id)
        raise ValueError("automatic resume is disabled in the Preview pilot; review the durable timeline and submit a new mission")

    def timeline(self, mission_id: str) -> tuple[dict[str, object], ...]:
        if self.coordinator.mission(mission_id) is None:
            raise KeyError(mission_id)
        result = []
        for item in self.coordinator.timeline(mission_id):
            payload = item.payload if isinstance(item.payload, dict) else {}
            result.append({
                "sequence": item.sequence,
                "event": item.kind,
                "state": str(payload.get("state", payload.get("decision", ""))),
                "timestamp": item.created_at,
                "execution_id": item.execution_id,
                "reason": _safe_text(payload.get("reason", ""), 1000),
                "output": _safe_text(payload.get("output", ""), 8192),
                "verification": _safe_text(payload.get("verification", ""), 200),
                "result": _safe_text(payload.get("result", ""), 200),
            })
        return tuple(result)

    def mission_memory(self, mission_id: str) -> tuple[dict[str, object], ...]:
        if self.coordinator.mission(mission_id) is None:
            raise KeyError(mission_id)
        result = []
        for item in self.coordinator.memory(mission_id):
            payload = item.get("payload")
            data = dict(payload) if isinstance(payload, dict) else {}
            data["metadata"] = {"mission_id": mission_id, "execution_id": item.get("execution_id", "")}
            result.append({
                "score": 1.0, "kind": item.get("kind", "episode"),
                "outcome": item.get("outcome", ""), "data": data,
            })
        return tuple(result[-5:])

    def pending_approvals(self) -> tuple[dict[str, object], ...]:
        return self.coordinator.pending_approvals()

    def approve_action(self, action_id: str) -> None:
        # The current hosted runner cannot execute approval-gated side effects.
        if any(item["id"] == action_id for item in self.pending_approvals()):
            raise ValueError("approval-gated execution is not enabled in the Preview read-only pilot")
        raise KeyError(action_id)

    def reject_action(self, action_id: str) -> HostedMissionRecord:
        mission_id = self.coordinator.reject_approval(action_id)
        record = self.store.get(mission_id)
        if record is None:
            raise CoordinatorUnavailable("rejected approval's mission is missing")
        return record

    def start(self) -> None:
        if not self.state_backend_ready or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_worker, args=(self._stop,),
            name="scout-postgres-mission-worker", daemon=True,
        )
        self._thread.start()

    def _run_worker(self, stop: threading.Event) -> None:
        last_recovery = time.monotonic()
        while not stop.is_set():
            try:
                result = self.runner.run_once()
                if time.monotonic() - last_recovery >= 60:
                    self.coordinator.recover_stale(older_than_seconds=600, max_items=50)
                    last_recovery = time.monotonic()
                if result is None:
                    stop.wait(1.0)
            except Exception as exc:
                # Never log task payloads, SQL, connection strings or secrets.
                print(f"PostgreSQL Mission Control worker paused ({type(exc).__name__})", flush=True)
                stop.wait(2.0)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None


__all__ = ["HostedMissionRecord", "PostgresHostedMissionController"]
