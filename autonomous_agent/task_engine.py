from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .capability_policy import Capability, check_capability
from .sandbox import run_safe_operation


class TaskIntent(str, Enum):
    TEST = "test"
    INSPECT = "inspect"
    AUDIT = "audit"
    DISCOVER = "discover"
    IMPROVE = "improve"
    FIX = "fix"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TaskPlan:
    raw_task: str
    intent: TaskIntent
    actions: tuple[str, ...]
    risk: str
    requires_approval: bool
    explanation: str


@dataclass(frozen=True)
class TaskResult:
    plan: TaskPlan
    status: str
    output: str


def plan_task(task: str) -> TaskPlan:
    """Map natural language to a bounded, allow-listed digital plan."""
    raw = " ".join(task.strip().split())
    text = raw.lower()
    if not raw:
        return TaskPlan("", TaskIntent.UNKNOWN, (), "none", False, "No task was supplied.")

    def has(*terms: str) -> bool:
        return any(term in text for term in terms)

    if has("test", "tests", "pytest", "check tests"):
        return TaskPlan(raw, TaskIntent.TEST, ("inspect", "test"), "low", False, "Inspect the workspace and run the test suite through the sandbox.")
    if has("audit", "review project", "review repo", "review repository"):
        return TaskPlan(raw, TaskIntent.AUDIT, ("inspect", "scout"), "low", False, "Inspect the workspace and run the existing audit pipeline.")
    if has("discover", "find ai", "find model", "find models", "new tools", "new ai", "free ai"):
        return TaskPlan(raw, TaskIntent.DISCOVER, ("scout",), "low", False, "Run the free-first discovery pipeline.")
    if has("inspect", "analyze", "analyse", "understand", "look at"):
        return TaskPlan(raw, TaskIntent.INSPECT, ("inspect",), "low", False, "Inspect repository structure and summarize actionable signals.")
    if has("fix", "bug", "debug", "repair"):
        return TaskPlan(raw, TaskIntent.FIX, ("inspect", "test"), "medium", True, "Inspect and test first; source changes require an approval-gated patch step.")
    if has("improve", "implement", "add feature", "add a feature", "change code", "refactor", "build"):
        return TaskPlan(raw, TaskIntent.IMPROVE, ("inspect", "test"), "medium", True, "Inspect and test first; implementation/deployment requires an approval-gated patch step.")
    return TaskPlan(raw, TaskIntent.UNKNOWN, ("inspect",), "low", False, "Inspect the workspace first because the request does not map to a known safe action.")


def _run_capability(operation: str, root: Path) -> str:
    try:
        capability = Capability(operation)
    except ValueError:
        return "capability blocked: unknown operation"
    decision = check_capability(capability, (capability,))
    if not decision.allowed:
        return f"capability blocked: {decision.reason}"
    result = run_safe_operation(operation, root)
    return result.output if result.success else f"{operation} failed: {result.output}"


def _inspect(root: Path) -> str:
    return _run_capability("inspect", root)


def _test(root: Path) -> str:
    return _run_capability("test", root)


def execute_task(task: str, root: Path) -> TaskResult:
    """Execute only non-destructive actions through the centralized capability/sandbox boundary."""
    plan = plan_task(task)
    if not plan.actions:
        return TaskResult(plan, "ignored", plan.explanation)

    outputs: list[str] = []
    for action in plan.actions:
        if action == "inspect":
            outputs.append(_inspect(root))
        elif action == "test":
            outputs.append(_test(root))
        elif action == "scout":
            outputs.append("The main scout pipeline will perform discovery/audit in this run.")

    if plan.requires_approval:
        outputs.append("WRITE/DEPLOY STEP BLOCKED: approval is required before modifying source, merging, or deploying.")
        status = "approval_required"
    else:
        status = "completed"
    return TaskResult(plan, status, "\n\n".join(outputs))
