from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .capability_policy import Capability
from .task_plan_models import PlanRisk, TaskIntent, TaskPlan, TaskStep, TaskAuditRecord


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


def build_workflow_plan(name: str, tasks: tuple[WorkflowTask, ...], granted: Iterable[Capability | str] = ()) -> TaskPlan:
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

    granted_values = {Capability(item) if not isinstance(item, Capability) else item for item in granted}
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
            if not visit(dependency.strip()):
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
            return _blocked(clean_name, f"capability is not granted: {task.capability.value}")
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
    audit = TaskAuditRecord(clean_name, TaskIntent.AUTOMATE, tuple(step.step_id for step in steps), True, digest)
    return TaskPlan(
        task=clean_name,
        intent=TaskIntent.AUTOMATE,
        steps=tuple(steps),
        risk=risk,
        executable=True,
        reason="workflow is dependency-valid and every capability is explicitly granted",
        audit=audit,
    )


def _blocked(name: str, reason: str) -> TaskPlan:
    digest = hashlib.sha256(json.dumps({"name": name, "reason": reason}, sort_keys=True).encode()).hexdigest()
    audit = TaskAuditRecord(name, TaskIntent.AUTOMATE, (), False, digest)
    return TaskPlan(name, TaskIntent.AUTOMATE, (), PlanRisk.CRITICAL, False, reason, audit)


def _rank(risk: PlanRisk) -> int:
    return {PlanRisk.LOW: 0, PlanRisk.MEDIUM: 1, PlanRisk.HIGH: 2, PlanRisk.CRITICAL: 3}[risk]
