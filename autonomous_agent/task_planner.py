from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .capability_policy import Capability
from .task_decomposer import decompose_task
from .task_intent import classify_intent
from .task_plan_models import PlanRisk, TaskAuditRecord, TaskPlan, TaskStep
from .task_risk import aggregate_risk
from .tool_registry import ToolRegistry, REGISTRY


_INTENT_TO_TOOLS: dict[str, tuple[str, ...]] = {
    "research": ("network.fetch",),
    "inspect": ("github.inspect", "filesystem.read"),
    "test": ("github.inspect", "tests.run"),
    "improve": ("github.inspect", "tests.run", "github.change"),
    "change": ("github.inspect", "tests.run", "github.change"),
    "automate": (),
    "unknown": (),
}


def _digest(task: str, intent: str, tool_names: tuple[str, ...]) -> str:
    payload = json.dumps({"task": task, "intent": intent, "tools": tool_names}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _select_tools(registry: ToolRegistry, intent: str) -> tuple:
    names = _INTENT_TO_TOOLS.get(intent, ())
    return tuple(tool for name in names if (tool := registry.get(name)) is not None)


def plan_task(
    task: str,
    *,
    granted: Iterable[Capability | str] = (),
    explicitly_approved: bool = False,
    sandbox_available: bool = True,
    audit_available: bool = True,
    registry: ToolRegistry = REGISTRY,
) -> TaskPlan:
    """Build an auditable plan using only tools and authorization from the Tool Registry."""
    raw = " ".join(task.strip().split())
    intent = classify_intent(raw)
    selected = _select_tools(registry, intent.value)
    descriptions = decompose_task(raw, intent)

    if intent.value in {"automate", "unknown"}:
        reason = "No registered executable tool mapping exists; plan fails closed."
        executable = False
    elif len(selected) != len(_INTENT_TO_TOOLS[intent.value]):
        reason = "A required tool is not registered; plan fails closed."
        executable = False
    else:
        executable = True
        reason = "All selected tools are registered; authorization will be checked before execution."

    steps: list[TaskStep] = []
    for index, (description, tool) in enumerate(zip(descriptions, selected), start=1):
        decision = registry.authorize(
            tool.name,
            granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
        )
        if not decision.allowed:
            executable = False
            reason = f"Authorization blocked for {tool.name}: {decision.reason}"
        steps.append(TaskStep(
            step_id=f"step-{index}",
            description=description,
            tool_name=tool.name,
            risk=PlanRisk(tool.risk_level.value),
            authorization="authorized" if decision.allowed else "blocked",
            execution_boundary="execute only through the existing registered capability/sandbox/lifecycle boundary",
            verification="verify tool result before proceeding and retain audit record",
        ))

    digest = _digest(raw, intent.value, tuple(step.tool_name for step in steps))
    audit = TaskAuditRecord(raw, intent, tuple(step.step_id for step in steps), executable, digest)
    return TaskPlan(raw, intent, tuple(steps), aggregate_risk(selected), executable, reason, audit)
