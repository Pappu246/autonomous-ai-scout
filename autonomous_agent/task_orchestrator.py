from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Protocol

from .capability_policy import Capability, CapabilityDecision
from .task_plan_models import TaskPlan
from .task_planner import plan_task
from .tool_registry import ToolRegistry, REGISTRY

_SECRET = re.compile(r"(?i)(?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+")


class OrchestrationState(str, Enum):
    PLANNED = "planned"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    REQUIRES_APPROVAL = "requires_approval"
    INTERRUPTED = "interrupted"
    VERIFICATION_FAILED = "verification_failed"


@dataclass(frozen=True)
class StructuredTask:
    task: str
    project: str | None = None
    metadata: Mapping[str, object] = None  # type: ignore[assignment]


@dataclass(frozen=True)
class StepRequirements:
    tool_name: str
    capability: str
    risk: str
    read_write: str
    network: str
    authentication: str
    sandbox: str
    approval: str


@dataclass(frozen=True)
class OrchestrationStep:
    step_id: str
    tool_name: str
    status: OrchestrationState
    requirements: StepRequirements
    reason: str


@dataclass(frozen=True)
class VerificationResult:
    success: bool
    status: str
    finding: str


@dataclass(frozen=True)
class OrchestrationReport:
    task_digest: str
    plan_digest: str
    objective: str
    intent: str
    project: str | None
    state: OrchestrationState
    steps: tuple[OrchestrationStep, ...]
    approval_required: bool
    selected_tools: tuple[str, ...]
    blocked_steps: tuple[str, ...]
    verification: VerificationResult | None = None

    @property
    def safe_dict(self) -> dict[str, object]:
        return {
            "task_digest": self.task_digest,
            "plan_digest": self.plan_digest,
            "objective": self.objective,
            "intent": self.intent,
            "project": self.project,
            "state": self.state.value,
            "steps": tuple({"step_id": s.step_id, "tool": s.tool_name, "status": s.status.value, "reason": s.reason} for s in self.steps),
            "approval_required": self.approval_required,
            "selected_tools": self.selected_tools,
            "blocked_steps": self.blocked_steps,
            "verification": None if self.verification is None else {
                "success": self.verification.success,
                "status": self.verification.status,
                "finding": self.verification.finding,
            },
        }


class MemoryEvidence(Protocol):
    def retrieve(self, project: str | None, task_digest: str) -> Iterable[Mapping[str, object]]: ...
    def store_safe(self, evidence: Mapping[str, object]) -> None: ...


class ConnectorDiscovery(Protocol):
    def list(self) -> Iterable[object]: ...
    def get(self, identity: str) -> object | None: ...
    def resolve_tool(self, identity: str, tool_name: str) -> object | None: ...


class ExistingExecutor(Protocol):
    """Existing Phase-G execution boundary; this module provides no implementation."""
    def execute_authorized(self, step_id: str, tool_name: str, task_digest: str) -> object: ...


class LifecycleBoundary(Protocol):
    """Adapter over the existing lifecycle system; never a second lifecycle implementation."""
    def transition(self, task_digest: str, state: OrchestrationState) -> bool: ...


class AuditBoundary(Protocol):
    """Adapter over existing audit storage/integrity mechanisms."""
    def record(self, event: Mapping[str, object]) -> None: ...


class TaskOrchestrator:
    """Coordinates existing planner, registries, policy, lifecycle, executor, memory and audit boundaries."""

    def __init__(self, *, registry: ToolRegistry = REGISTRY, connector_registry: ConnectorDiscovery | None = None, memory: MemoryEvidence | None = None, lifecycle: LifecycleBoundary | None = None, audit: AuditBoundary | None = None):
        self._registry = registry
        self._connectors = connector_registry
        self._memory = memory
        self._lifecycle = lifecycle
        self._audit = audit

    @staticmethod
    def _safe_task(task: StructuredTask | str) -> StructuredTask:
        if isinstance(task, str):
            task = StructuredTask(task)
        if not isinstance(task, StructuredTask) or not isinstance(task.task, str):
            raise ValueError("task must be a structured task or string")
        cleaned = _SECRET.sub("[REDACTED]", " ".join(task.task.strip().split()))
        if not cleaned:
            raise ValueError("task must be non-empty")
        project = task.project.strip() if isinstance(task.project, str) and task.project.strip() else None
        return StructuredTask(cleaned, project, {})

    @staticmethod
    def _digest(value: object) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _tool_requirements(tool: object) -> StepRequirements:
        return StepRequirements(str(tool.name), str(tool.capability), str(tool.risk_level.value), str(tool.read_write_mode.value), str(tool.network_requirement.value), str(tool.authentication_requirement.value), str(tool.sandbox_requirement.value), str(tool.approval_requirement.value))

    def _discover(self, tool_name: str) -> bool:
        if self._connectors is None:
            return True
        for spec in self._connectors.list():
            if getattr(spec, "enabled", False) and self._connectors.resolve_tool(getattr(spec, "identity", ""), tool_name) is not None:
                return True
        return False

    def plan(self, task: StructuredTask | str, *, granted: Iterable[Capability | str] = (), explicitly_approved: bool = False, sandbox_available: bool = True, audit_available: bool = True) -> tuple[StructuredTask, TaskPlan]:
        safe = self._safe_task(task)
        return safe, plan_task(safe.task, granted=granted, explicitly_approved=explicitly_approved, sandbox_available=sandbox_available, audit_available=audit_available, registry=self._registry)

    def orchestrate(self, task: StructuredTask | str, *, granted: Iterable[Capability | str] = (), explicitly_approved: bool = False, sandbox_available: bool = True, audit_available: bool = True) -> OrchestrationReport:
        safe, plan = self.plan(task, granted=granted, explicitly_approved=explicitly_approved, sandbox_available=sandbox_available, audit_available=audit_available)
        task_digest = self._digest({"task": safe.task, "project": safe.project})
        if self._memory is not None:
            tuple(self._memory.retrieve(safe.project, task_digest))

        steps: list[OrchestrationStep] = []
        approval_required = False
        for planned in plan.steps:
            tool = self._registry.get(planned.tool_name)
            if tool is None:
                steps.append(OrchestrationStep(planned.step_id, planned.tool_name, OrchestrationState.BLOCKED, StepRequirements(planned.tool_name, "", "", "", "", "", "", ""), "tool is not registered"))
                continue
            requirements = self._tool_requirements(tool)
            decision: CapabilityDecision = self._registry.authorize(planned.tool_name, granted, explicitly_approved=explicitly_approved, sandbox_available=sandbox_available, audit_available=audit_available)
            if not self._discover(planned.tool_name):
                decision = CapabilityDecision(False, "no enabled connector exposes the registered tool", planned.tool_name)
            if requirements.approval != "none" and not explicitly_approved:
                approval_required = True
            status = OrchestrationState.AUTHORIZED if decision.allowed else (OrchestrationState.REQUIRES_APPROVAL if "approval" in decision.reason.lower() else OrchestrationState.BLOCKED)
            steps.append(OrchestrationStep(planned.step_id, planned.tool_name, status, requirements, decision.reason))

        blocked = tuple(s.step_id for s in steps if s.status in {OrchestrationState.BLOCKED, OrchestrationState.REQUIRES_APPROVAL})
        state = OrchestrationState.REQUIRES_APPROVAL if any(s.status is OrchestrationState.REQUIRES_APPROVAL for s in steps) else (OrchestrationState.BLOCKED if blocked or not plan.executable else OrchestrationState.AUTHORIZED)
        report = OrchestrationReport(task_digest, plan.audit.plan_digest, safe.task, plan.intent.value, safe.project, state, tuple(steps), approval_required, tuple(s.tool_name for s in plan.steps), blocked)
        self._record_audit(report, "planned")
        if self._lifecycle is not None:
            self._lifecycle.transition(task_digest, state)
        return report

    def execute(self, report: OrchestrationReport, executor: ExistingExecutor | None = None) -> OrchestrationReport:
        if report.state is not OrchestrationState.AUTHORIZED:
            return report
        if executor is None:
            return self._with_state(report, OrchestrationState.BLOCKED, "existing executor boundary was not supplied")
        if self._lifecycle is not None and not self._lifecycle.transition(report.task_digest, OrchestrationState.RUNNING):
            return self._with_state(report, OrchestrationState.BLOCKED, "existing lifecycle boundary rejected execution")
        for step in report.steps:
            try:
                result = executor.execute_authorized(step.step_id, step.tool_name, report.task_digest)
            except InterruptedError:
                return self._with_state(report, OrchestrationState.INTERRUPTED, "execution interrupted; no automatic replay")
            except Exception as exc:
                return self._with_state(report, OrchestrationState.FAILED, f"existing executor failed: {type(exc).__name__}")
            if result is None:
                return self._with_state(report, OrchestrationState.VERIFICATION_FAILED, "executor returned no verifiable result")
        verification = VerificationResult(True, "verified", "existing executor returned results for every authorized step")
        completed = OrchestrationReport(report.task_digest, report.plan_digest, report.objective, report.intent, report.project, OrchestrationState.SUCCEEDED, report.steps, report.approval_required, report.selected_tools, report.blocked_steps, verification)
        self._record_audit(completed, "completed")
        if self._memory is not None:
            self._memory.store_safe({"task_digest": report.task_digest, "plan_digest": report.plan_digest, "result": "succeeded", "verification": verification.status, "tools": report.selected_tools})
        if self._lifecycle is not None:
            self._lifecycle.transition(report.task_digest, OrchestrationState.SUCCEEDED)
        return completed

    def _with_state(self, report: OrchestrationReport, state: OrchestrationState, reason: str) -> OrchestrationReport:
        updated = OrchestrationReport(report.task_digest, report.plan_digest, report.objective, report.intent, report.project, state, report.steps, report.approval_required, report.selected_tools, report.blocked_steps, VerificationResult(False, state.value, reason) if state in {OrchestrationState.FAILED, OrchestrationState.INTERRUPTED, OrchestrationState.VERIFICATION_FAILED} else report.verification)
        self._record_audit(updated, state.value)
        return updated

    def _record_audit(self, report: OrchestrationReport, event: str) -> None:
        if self._audit is None:
            return
        self._audit.record({"event": event, "task_digest": report.task_digest, "plan_digest": report.plan_digest, "step_ids": tuple(s.step_id for s in report.steps), "state": report.state.value})
