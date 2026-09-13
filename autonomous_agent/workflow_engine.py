from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Mapping

from .capability_policy import Capability
from .task_plan_models import PlanRisk, TaskAuditRecord, TaskIntent, TaskPlan, TaskStep
from .tool_registry import RiskLevel, ToolRegistry, REGISTRY

MAX_WORKFLOW_STEPS = 12
MAX_TASK_LENGTH = 4096
MAX_STEP_DESCRIPTION = 512

_READ_INTENTS = {
    "research": TaskIntent.RESEARCH,
    "web": TaskIntent.RESEARCH,
    "browse": TaskIntent.RESEARCH,
    "browser": TaskIntent.RESEARCH,
    "file": TaskIntent.WORKSPACE,
    "workspace": TaskIntent.WORKSPACE,
    "email": TaskIntent.EMAIL,
    "mail": TaskIntent.EMAIL,
    "calendar": TaskIntent.CALENDAR,
    "inspect": TaskIntent.INSPECT,
    "test": TaskIntent.TEST,
    "improve": TaskIntent.IMPROVE,
    "change": TaskIntent.CHANGE,
    "automate": TaskIntent.AUTOMATE,
}


def _intent(task: str, tools: tuple[str, ...]) -> TaskIntent:
    lowered = task.lower()
    for marker, value in _READ_INTENTS.items():
        if marker in lowered:
            return value
    if any(name.startswith("browser.") or name.startswith("web.") for name in tools):
        return TaskIntent.RESEARCH
    if any(name.startswith("email.") for name in tools):
        return TaskIntent.EMAIL
    if any(name.startswith("calendar.") for name in tools):
        return TaskIntent.CALENDAR
    return TaskIntent.UNKNOWN


def _plan_risk(levels: Iterable[RiskLevel]) -> PlanRisk:
    ranks = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
    highest = max((ranks[level] for level in levels), default=0)
    return (PlanRisk.LOW, PlanRisk.MEDIUM, PlanRisk.HIGH, PlanRisk.CRITICAL)[highest]


def _digest(task: str, steps: tuple[TaskStep, ...], risk: PlanRisk, authorized: bool) -> str:
    payload = {
        "task": task,
        "steps": [step.__dict__ for step in steps],
        "risk": risk.value,
        "authorized": authorized,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    task: str
    tool_names: tuple[str, ...]
    inputs: tuple[Mapping[str, object], ...] = ()


class WorkflowEngine:
    """Compose registered tools into a bounded TaskPlan; execution stays with the existing Safe Executor."""

    def __init__(self, registry: ToolRegistry = REGISTRY):
        self._registry = registry

    def plan(self, workflow: WorkflowDefinition, *, granted: Iterable[Capability | str] = ()) -> TaskPlan:
        if not workflow.name.strip():
            return self._blocked(workflow.task, "workflow name is required")
        if not workflow.task.strip() or len(workflow.task) > MAX_TASK_LENGTH:
            return self._blocked(workflow.task, "workflow task is empty or too long")
        if not workflow.tool_names or len(workflow.tool_names) > MAX_WORKFLOW_STEPS:
            return self._blocked(workflow.task, "workflow step count is outside the bounded limit")

        normalized_grants = tuple(granted)
        steps: list[TaskStep] = []
        errors: list[str] = []
        for index, tool_name in enumerate(workflow.tool_names, start=1):
            tool = self._registry.get(tool_name)
            if tool is None:
                errors.append(f"unknown tool: {tool_name}")
                continue
            decision = self._registry.authorize(
                tool.name,
                normalized_grants,
                explicitly_approved=False,
                sandbox_available=True,
                audit_available=True,
            )
            steps.append(
                TaskStep(
                    step_id=f"{workflow.name}:{index}",
                    description=tool.description[:MAX_STEP_DESCRIPTION],
                    tool_name=tool.name,
                    risk=PlanRisk(tool.risk_level.value),
                    authorization=decision.reason,
                    execution_boundary="Tool Registry → Capability Policy → Safe Executor → Sandbox",
                    verification="tool result must be verified by the existing executor",
                )
            )
            if not decision.allowed:
                errors.append(f"{tool.name}: {decision.reason}")

        frozen_steps = tuple(steps)
        risk = _plan_risk(tuple(self._registry.get(name).risk_level for name in workflow.tool_names if self._registry.get(name)))
        intent = _intent(workflow.task, workflow.tool_names)
        authorized = not errors and bool(frozen_steps)
        reason = "workflow is bounded and every step is authorized" if authorized else "; ".join(errors) or "workflow has no valid steps"
        digest = _digest(workflow.task, frozen_steps, risk, authorized)
        return TaskPlan(
            task=workflow.task,
            intent=intent,
            steps=frozen_steps,
            risk=risk,
            executable=authorized,
            reason=reason,
            audit=TaskAuditRecord(
                task=workflow.task,
                intent=intent,
                step_ids=tuple(step.step_id for step in frozen_steps),
                authorized=authorized,
                plan_digest=digest,
            ),
        )

    @staticmethod
    def _blocked(task: str, reason: str) -> TaskPlan:
        digest = hashlib.sha256(task.encode("utf-8")).hexdigest()
        audit = TaskAuditRecord(task, TaskIntent.UNKNOWN, (), False, digest)
        return TaskPlan(task, TaskIntent.UNKNOWN, (), PlanRisk.LOW, False, reason, audit)
