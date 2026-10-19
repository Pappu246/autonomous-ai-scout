"""Local-only read-only E2E runner over a pre-provisioned PostgreSQL coordinator.

This is deliberately not wired into the hosted MissionController and refuses Vercel/Blob runtime markers. It exercises
an end-to-end slice against a caller-supplied coordinator while the existing
hosted readiness gate remains closed until every state path is migrated.
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from threading import Event, Thread
from typing import Iterator

from .execution_engine import ExecutionResult, ExecutionState, _authorization_digest
from .filesystem_workspace import WorkspaceConnector
from .task_completion import criteria_for, requested_read_path
from .postgres_coordinator import CoordinatorUnavailable, PostgresMissionCoordinator, Submission, _id
from .postgres_execution_audit import PostgresExecutionAuditStore
from .postgres_execution_checkpoint import PostgresExecutionCheckpointStore
from .run_journal import _safe_text, make_run_record
from .task_core import AutonomousTaskCore, CanonicalTask
from .task_queue import _safe_task
from .vercel_state import require_atomic_state_coordinator


# Keep a live, fenced worker claim fresh well before the coordinator's minimum
# stale-recovery age. Tests may shorten this interval to exercise the loop.
CLAIM_HEARTBEAT_INTERVAL_SECONDS = 10.0


class PostgresMissionRunner:
    """Execute safe, local read-only missions on a pre-provisioned coordinator.

    Submission and terminal completion use PostgreSQL transactions. This pilot
    rejects plans needing network, mutation, or human approval. It intentionally
    does not replace MissionController, approvals, background recovery, or the
    hosted HTTP service.
    """

    def __init__(
        self,
        coordinator: PostgresMissionCoordinator,
        *,
        root: str | Path,
        worker_id: str | None = None,
        core: AutonomousTaskCore | None = None,
        workspace_connector: WorkspaceConnector | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.root = Path(root).resolve()
        self.worker_id = _id(worker_id or "worker-" + uuid.uuid4().hex, "worker ID")
        self.core = core or AutonomousTaskCore()
        self.workspace_connector = workspace_connector or WorkspaceConnector(self.root)
        self.audit_path = self.root / "state" / "runtime_execution.jsonl"
        self._require_available()

    def _require_available(self) -> None:
        # The coordinator is explicit, but this pilot must not become a hidden
        # bypass around the existing hosted/Blob fail-closed gate.
        require_atomic_state_coordinator()
        self.coordinator.check_ready()

    @contextmanager
    def _maintain_claim(self, claim) -> Iterator[None]:
        """Heartbeat a running claim while the bounded read-only executor works.

        A failed heartbeat is treated as loss of authority. The caller must not
        proceed to terminal state, journal or memory writes after that failure.
        This pilot permits only local reads, so it never tries to cancel or replay
        an external side effect.
        """
        stop = Event()
        heartbeat_failed: list[bool] = []

        def heartbeat_loop() -> None:
            while not stop.wait(CLAIM_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    self.coordinator.heartbeat(claim)
                except Exception:
                    # Do not emit provider/connection diagnostics or credentials.
                    heartbeat_failed.append(True)
                    stop.set()
                    return

        thread = Thread(
            target=heartbeat_loop,
            name="scout-postgres-claim-heartbeat",
            daemon=True,
        )
        thread.start()
        try:
            yield
        except BaseException:
            stop.set()
            thread.join()
            raise
        else:
            stop.set()
            thread.join()
            if heartbeat_failed:
                raise CoordinatorUnavailable(
                    "worker claim heartbeat failed; manual reconciliation required"
                ) from None

    def _prepare_read_only(self, task: str) -> tuple[CanonicalTask, str, str, str, bool]:
        cleaned = _safe_task(task)
        prepared = self.core.prepare(cleaned, explicitly_approved=False)
        if not prepared.plan.executable:
            raise ValueError("mission plan is not executable: " + _safe_text(prepared.plan.reason, 300))
        if not prepared.plan.steps:
            raise ValueError("mission plan must contain at least one step")
        # The initial coordinator runner is intentionally narrower than the
        # general tool policy: no networked reads, writes, or approval flows.
        for step in prepared.plan.steps:
            tool = self.core._registry.get(step.tool_name)
            if tool is None:
                raise ValueError("mission plan references an unknown tool")
            if (
                not tool.safe_autonomous
                or tool.read_write_mode.value != "read_only"
                or tool.network_requirement.value != "none"
                or tool.approval_requirement.value != "none"
            ):
                raise ValueError("PostgreSQL pilot accepts only autonomous local read-only plans")
        task_digest = hashlib.sha256(prepared.plan.task.encode("utf-8")).hexdigest()
        authorization_digest = _authorization_digest(
            prepared.granted,
            False,
            prepared.plan,
            registry=self.core._registry,
        )
        criteria = criteria_for(prepared.plan.task, root=self.root)
        return prepared, task_digest, authorization_digest, criteria.digest, criteria.requires_output

    def _workspace_request(self, task: str) -> dict[str, object]:
        """Derive only explicit local read/list parameters; never accept a write."""
        prepared = self.core.prepare(task, explicitly_approved=False)
        names = tuple(step.tool_name for step in prepared.plan.steps)
        if names == ("filesystem.read",):
            path = requested_read_path(task, self.root)
            if not path:
                raise ValueError("local file reads require an explicit bounded path")
            return {"filesystem.read": {"operation": "read", "path": path}}
        if names == ("filesystem.list",):
            return {"filesystem.list": {"operation": "list", "path": "."}}
        return {}

    def submit(self, task: str, *, idempotency_key: str) -> Submission:
        """Submit one explicitly identified read-only request; never key by task text."""
        self._require_available()
        prepared, task_digest, authorization_digest, criteria_digest, requires_output = (
            self._prepare_read_only(task)
        )
        plan_digest = prepared.plan.audit.plan_digest
        request = {
            "task_digest": task_digest,
            "plan_digest": plan_digest,
            "authorization_digest": authorization_digest,
            "criteria_digest": criteria_digest,
            "requires_output": requires_output,
        }
        mission = {
            "task": prepared.plan.task,
            "task_digest": task_digest,
            "plan_digest": plan_digest,
            "authorization_digest": authorization_digest,
            "criteria_digest": criteria_digest,
            "requires_output": requires_output,
        }
        queue = {"task": prepared.plan.task, "task_digest": task_digest}
        return self.coordinator.submit(
            idempotency_key,
            request,
            mission,
            queue,
            requires_output=requires_output,
            requires_approval=False,
        )

    def run_once(self) -> ExecutionResult | None:
        """Claim one item, execute it, and transactionally commit its terminal result."""
        self._require_available()
        claim = self.coordinator.claim_next(self.worker_id)
        if claim is None:
            return None
        mission = self.coordinator.mission(claim.mission_id)
        if mission is None or not isinstance(mission.get("payload"), dict):
            raise CoordinatorUnavailable("claimed mission has no trusted payload; reconciliation required")
        payload = mission["payload"]
        task = payload.get("task")
        if not isinstance(task, str) or claim.payload.get("task") != task:
            raise CoordinatorUnavailable("claimed mission and queue payload disagree; reconciliation required")

        prepared, task_digest, authorization_digest, criteria_digest, requires_output = (
            self._prepare_read_only(task)
        )
        if (
            payload.get("task_digest") != task_digest
            or payload.get("plan_digest") != prepared.plan.audit.plan_digest
            or payload.get("authorization_digest") != authorization_digest
            or payload.get("criteria_digest") != criteria_digest
            or payload.get("requires_output") is not requires_output
        ):
            raise CoordinatorUnavailable("mission policy/plan identity changed after submission; reconciliation required")

        audit_store = PostgresExecutionAuditStore(self.coordinator, claim)
        checkpoint_store = PostgresExecutionCheckpointStore(self.coordinator, claim)
        with self._maintain_claim(claim):
            result = self.core.execute(
                prepared,
                self.root,
                audit_path=self.audit_path,
                execution_id=claim.execution_id,
                checkpoint_store=checkpoint_store,
                audit_store=audit_store,
                workspace_connector=self.workspace_connector,
                workspace_request=self._workspace_request(task),
                explicitly_approved=False,
            )
        criteria = criteria_for(task, root=self.root)
        validation_event_id = None
        if result.state is ExecutionState.VERIFIED:
            evidence = audit_store.events(claim.execution_id)
            if requires_output:
                expected_digest = hashlib.sha256(result.output.encode("utf-8")).hexdigest()
                validation_event_id = next((
                    item["audit_event_id"] for item in reversed(evidence)
                    if item.get("event") == "output_validated"
                    and item.get("criteria_digest") == criteria.digest
                    and item.get("output_sha256") == expected_digest
                ), None)
                if not result.output or validation_event_id is None:
                    raise CoordinatorUnavailable("verified output lacks matching PostgreSQL audit evidence")
            else:
                validation_event_id = next((
                    item["audit_event_id"] for item in reversed(evidence)
                    if item.get("event") == "tool_result"
                    and item.get("result") == "success"
                    and item.get("verification") == "verified"
                ), None)
                if validation_event_id is None:
                    raise CoordinatorUnavailable("verified read lacks matching PostgreSQL tool evidence")

        self.coordinator.append_claim_event(
            claim,
            "execution-result-" + uuid.uuid4().hex,
            "execution_result",
            claim.execution_id,
            {
                "state": result.state.value,
                "reason": _safe_text(result.reason, 1000),
                "attempts": str(result.attempts),
            },
        )
        if result.state is ExecutionState.RECOVERY_REQUIRED:
            # Recovery stays a human/reconciliation action; this runner never auto-replays.
            self.coordinator.record_run(
                claim,
                asdict(make_run_record(execution_id=claim.execution_id, task=task, result=result)),
            )
            return result

        terminal_state = result.state.value if result.state.value in {"verified", "partial", "failed"} else "failed"
        finish_args: dict[str, object] = {}
        finish_args["run_record"] = asdict(
            make_run_record(execution_id=claim.execution_id, task=task, result=result)
        )
        if result.state is ExecutionState.VERIFIED:
            finish_args["validation_event_id"] = validation_event_id
            if requires_output:
                finish_args["validated_output"] = result.output
            finish_args["memory"] = {
                "summary": result.output if requires_output else _safe_text(result.reason, 1000),
                "attempts": result.attempts,
                "task_digest": task_digest,
            }
        self.coordinator.finish(claim, terminal_state, **finish_args)
        return result


__all__ = ["PostgresMissionRunner"]
