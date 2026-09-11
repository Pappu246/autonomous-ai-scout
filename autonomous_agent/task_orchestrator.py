from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Protocol

from .capability_policy import Capability
from .task_planner import plan_task
from .task_plan_models import TaskPlan
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
class OrchestrationStep:
    step_id: str
    tool_name: str
    status: OrchestrationState
    reason: str


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
        }


class MemoryEvidence(Protocol):
    def learn(self, project: str, kind: str) -> object: ...


class ConnectorDiscovery(Protocol):
    def get(self, identity: str) -> object | None: ...
    def resolve_tool(self, identity: str, tool_name: str) -> object | None: ...


class ExistingExecutor(Protocol):
    def execute(self, *args: object, **kwargs: object) -> object: ...


class TaskOrchestrator:
    """Coordinates existing planner/policy/executor boundaries; it owns no authority."""

    def __init__(self, *, registry: ToolRegistry = REGISTRY, connector_registry: ConnectorDiscovery | None = None, memory: MemoryEvidence | None = None):
        self._registry = registry
        self._connectors = connector_registry
        self._memory = memory

    @staticmethod
    def _safe_task(task: StructuredTask | str) -> StructuredTask:
        if isinstance(task, str):
            task = StructuredTask(task)
        if not isinstance(task, StructuredTask) or not isinstance(task.task, str):
            raise ValueError("task must be a structured task or string")
        cleaned = _SECRET.sub("[REDACTED]", " ".join(task.task.strip().split()))
        if not cleaned:
            raise ValueError("task must be non-empty")
        return StructuredTask(cleaned, task.project, {})

    @staticmethod
    def _digest(value: object) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def plan(self, task: StructuredTask | str, *, granted: Iterable[Capability | str] = (), explicitly_approved: bool = False, sandbox_available: bool = True, audit_available: bool = True) -> tuple[StructuredTask, TaskPlan]:
        safe = self._safe_task(task)
        plan = plan_task(safe.task, granted=granted, explicitly_approved=explicitly_approved, sandbox_available=sandbox_available, audit_available=audit_available, registry=self._registry)
        return safe, plan

    def orchestrate(self, task: StructuredTask | str, *, granted: Iterable[Capability | str] = (), explicitly_approved: bool = False, sandbox_available: bool = True, audit_available: bool = True) -> OrchestrationReport:
        safe, plan = self.plan(task, granted=granted, explicitly_approved=explicitly_approved, sandbox_available=sandbox_available, audit_available=audit_available)
        steps: list[OrchestrationStep] = []
        for step in plan.steps:
            status = OrchestrationState.AUTHORIZED if step.authorization == "authorized" else OrchestrationState.BLOCKED
            reason = "authorized by current Tool Registry policy" if status is OrchestrationState.AUTHORIZED else "blocked by current Tool Registry policy"
            if self._connectors is not None:
                # Connector metadata can only further constrain a registered tool.
                exposed = False
                for connector_id in ("github", "web_research", "files", "ai_providers", "project_repository"):
                    if self._connectors.get(connector_id) is not None and self._connectors.resolve_tool(connector_id, step.tool_name) is not None:
                        exposed = True
                        break
                if not exposed:
                    status, reason = OrchestrationState.BLOCKED, "no enabled connector exposes the registered tool"
            steps.append(OrchestrationStep(step.step_id, step.tool_name, status, reason))
        blocked = tuple(s.step_id for s in steps if s.status is OrchestrationState.BLOCKED)
        approval = any(s.authorization != "authorized" and "approval" in plan.reason.lower() for s in plan.steps) or "approval" in plan.reason.lower()
        state = OrchestrationState.BLOCKED if blocked or not plan.executable else OrchestrationState.AUTHORIZED
        return OrchestrationReport(self._digest(safe.task), plan.audit.plan_digest, safe.task, plan.intent.value, safe.project, state, tuple(steps), approval, tuple(s.tool_name for s in plan.steps), blocked)

    def execute(self, report: OrchestrationReport, executor: ExistingExecutor | None = None) -> OrchestrationReport:
        if report.state is not OrchestrationState.AUTHORIZED:
            return report
        if executor is None:
            return OrchestrationReport(report.task_digest, report.plan_digest, report.objective, report.intent, report.project, OrchestrationState.BLOCKED, report.steps, report.approval_required, report.selected_tools, report.blocked_steps)
        # The orchestrator never invokes tools itself; callers supply the already-approved existing executor.
        return report
