from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .capability_policy import Capability
from .task_plan_models import PlanRisk, TaskAuditRecord, TaskIntent, TaskPlan, TaskStep

MAX_WORKFLOW_STEPS = 12
MAX_WORKFLOW_TEXT = 512


class WorkflowState(str, Enum):
    PLANNED = "planned"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WorkflowTask:
    name: str
    tool_name: str
    capability: Capability
    intent: TaskIntent
    risk: PlanRisk = PlanRisk.LOW
    depends_on: tuple[str, ...] = ()
    verification: str = "verified"


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    task: str
    tool_names: tuple[str, ...]


def workflow_digest(name: str, tasks: Iterable[WorkflowTask]) -> str:
    payload = [
        {
            "name": task.name,
            "tool_name": task.tool_name,
            "capability": task.capability.value,
            "intent": task.intent.value,
            "risk": task.risk.value,
            "depends_on": list(task.depends_on),
        }
        for task in tasks
    ]
    raw = json.dumps({"name": name, "tasks": payload}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_workflow_plan(
    name: str,
    tasks: tuple[WorkflowTask, ...],
    granted: Iterable[Capability | str] = (),
) -> TaskPlan:
    clean_name = name.strip()
    if not clean_name or len(clean_name) > MAX_WORKFLOW_TEXT:
        return _blocked(clean_name or name, "workflow name is invalid")
    if not tasks:
        return _blocked(clean_name, "workflow must contain at least one task")
    if len(tasks) > MAX_WORKFLOW_STEPS:
        return _blocked(clean_name, "workflow exceeds the step limit")

    by_name: dict[str, WorkflowTask] = {}
    for task in tasks:
        key = task.name.strip()
        if not key or len(key) > MAX_WORKFLOW_TEXT or key in by_name:
            return _blocked(clean_name, "workflow contains an invalid or duplicate task name")
        by_name[key] = task

    try:
        granted_values = {
            Capability(item) if not isinstance(item, Capability) else item for item in granted
        }
    except (TypeError, ValueError):
        return _blocked(clean_name, "workflow capability grant is invalid")

    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> bool:
        if key in visiting:
            return False
        if key in visited:
            return True
        task = by_name.get(key)
        if task is None:
            return False
        visiting.add(key)
        for dependency in task.depends_on:
            dependency_key = dependency.strip()
            if not dependency_key or not visit(dependency_key):
                return False
        visiting.remove(key)
        visited.add(key)
        ordered.append(key)
        return True

    for key in by_name:
        if not visit(key):
            return _blocked(clean_name, "workflow dependencies are invalid or cyclic")

    steps: list[TaskStep] = []
    risk = PlanRisk.LOW
    for index, key in enumerate(ordered, 1):
        task = by_name[key]
        if task.capability not in granted_values:
            return _blocked(
                clean_name,
                f"capability is not granted: {task.tool_name} ({task.capability.value})",
            )
        if _rank(task.risk) > _rank(risk):
            risk = task.risk
        steps.append(
            TaskStep(
                step_id=f"wf-{index:02d}-{hashlib.sha256(key.encode()).hexdigest()[:8]}",
                description=f"{task.name}: {task.tool_name}",
                tool_name=task.tool_name,
                risk=task.risk,
                authorization="registry_and_capability_policy",
                execution_boundary="existing_safe_executor",
                verification=task.verification,
            )
        )

    digest = workflow_digest(clean_name, tasks)
    audit = TaskAuditRecord(
        clean_name,
        TaskIntent.AUTOMATE,
        tuple(step.step_id for step in steps),
        True,
        digest,
    )
    return TaskPlan(
        clean_name,
        TaskIntent.AUTOMATE,
        tuple(steps),
        risk,
        True,
        "workflow is dependency-valid and every capability is explicitly granted",
        audit,
    )


class WorkflowEngine:
    """Compose registered tools into a bounded plan without executing them."""

    def __init__(self, registry=None):
        if registry is None:
            from .tool_registry import REGISTRY

            registry = REGISTRY
        self.registry = registry

    def plan(
        self,
        workflow: WorkflowDefinition,
        granted: Iterable[Capability | str] = (),
    ) -> TaskPlan:
        if not isinstance(workflow, WorkflowDefinition):
            return _blocked("", "workflow definition is invalid")
        name = workflow.name.strip()
        task_text = " ".join(workflow.task.strip().split())
        if not name or len(name) > MAX_WORKFLOW_TEXT:
            return _blocked(name, "workflow name is invalid")
        if not task_text or len(task_text) > MAX_WORKFLOW_TEXT:
            return _blocked(name, "workflow task is invalid")
        if not workflow.tool_names or len(workflow.tool_names) > MAX_WORKFLOW_STEPS:
            return _blocked(
                name,
                "workflow tool list is invalid or exceeds the step limit",
            )
        try:
            normalized_grants = tuple(
                Capability(item) if not isinstance(item, Capability) else item
                for item in granted
            )
        except (TypeError, ValueError):
            return _blocked(name, "workflow capability grant is invalid")

        tasks: list[WorkflowTask] = []
        for index, raw_name in enumerate(workflow.tool_names, 1):
            tool_name = raw_name.strip().lower()
            spec = self.registry.get(tool_name) if hasattr(self.registry, "get") else None
            if spec is None:
                return _blocked(name, f"tool is not registered: {tool_name}")
            try:
                capability = Capability(spec.capability)
            except ValueError:
                return _blocked(name, f"tool has invalid capability: {tool_name}")
            approval = getattr(spec, "approval_requirement", None)
            if approval is not None and getattr(approval, "value", str(approval)) != "none":
                return _blocked(name, f"tool requires explicit approval: {tool_name}")
            try:
                risk = PlanRisk(spec.risk_level.value)
            except (ValueError, AttributeError):
                risk = PlanRisk.MEDIUM
            tasks.append(
                WorkflowTask(
                    name=f"step-{index:02d}",
                    tool_name=tool_name,
                    capability=capability,
                    intent=_intent_for_tool(tool_name),
                    risk=risk,
                    verification="verified_by_safe_executor",
                )
            )
        return build_workflow_plan(name, tuple(tasks), normalized_grants)


def _intent_for_tool(tool_name: str) -> TaskIntent:
    if tool_name.startswith("web.") or tool_name.startswith("browser."):
        return TaskIntent.RESEARCH
    if tool_name.startswith("email."):
        return TaskIntent.EMAIL
    if tool_name.startswith("calendar."):
        return TaskIntent.CALENDAR
    if tool_name.startswith("filesystem."):
        return TaskIntent.WORKSPACE
    if tool_name.startswith("tests."):
        return TaskIntent.TEST
    if tool_name.startswith("github."):
        return TaskIntent.INSPECT
    return TaskIntent.AUTOMATE


def _blocked(name: str, reason: str) -> TaskPlan:
    digest = hashlib.sha256(
        json.dumps({"name": name, "reason": reason}, sort_keys=True).encode()
    ).hexdigest()
    audit = TaskAuditRecord(name, TaskIntent.AUTOMATE, (), False, digest)
    return TaskPlan(name, TaskIntent.AUTOMATE, (), PlanRisk.CRITICAL, False, reason, audit)


def _rank(risk: PlanRisk) -> int:
    return {
        PlanRisk.LOW: 0,
        PlanRisk.MEDIUM: 1,
        PlanRisk.HIGH: 2,
        PlanRisk.CRITICAL: 3,
    }[risk]
