from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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
    """Turn a natural-language digital request into a bounded execution plan.

    This is deliberately deterministic when no LLM is configured. It prevents
    arbitrary shell commands from being inferred from user text.
    """
    raw = " ".join(task.strip().split())
    text = raw.lower()
    if not raw:
        return TaskPlan("", TaskIntent.UNKNOWN, (), "none", False, "No task was supplied.")

    def has(*terms: str) -> bool:
        return any(term in text for term in terms)

    if has("test", "tests", "pytest", "check tests"):
        return TaskPlan(raw, TaskIntent.TEST, ("inspect", "test"), "low", False, "Inspect the workspace and run the test suite.")
    if has("audit", "review project", "review repo", "review repository"):
        return TaskPlan(raw, TaskIntent.AUDIT, ("inspect", "audit"), "low", False, "Inspect the repository and run the existing project audit.")
    if has("discover", "find ai", "find model", "find models", "new tools"):
        return TaskPlan(raw, TaskIntent.DISCOVER, ("discover",), "low", False, "Run the free-first discovery and verification pipeline.")
    if has("inspect", "analyze", "analyse", "understand", "look at"):
        return TaskPlan(raw, TaskIntent.INSPECT, ("inspect",), "low", False, "Inspect repository structure and summarize actionable signals.")
    if has("fix", "bug", "debug", "repair"):
        return TaskPlan(raw, TaskIntent.FIX, ("inspect", "test"), "medium", True, "Inspect and test first; code changes require an approval-gated patch step.")
    if has("improve", "implement", "add feature", "change code", "refactor", "build"):
        return TaskPlan(raw, TaskIntent.IMPROVE, ("inspect", "test"), "medium", True, "Inspect and test first; implementation/deployment requires an approval-gated patch step.")
    return TaskPlan(raw, TaskIntent.UNKNOWN, ("inspect",), "low", False, "Inspect the workspace first because the request does not map to a known safe action.")


def _inspect(root: Path) -> str:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts and not any(part in {"__pycache__", ".pytest_cache", "node_modules"} for part in path.parts):
            files.append(str(path.relative_to(root)))
    preview = files[:80]
    suffix = f"\n... and {len(files) - len(preview)} more files" if len(files) > len(preview) else ""
    return "Workspace files:\n" + "\n".join(f"- {item}" for item in preview) + suffix


def _test(root: Path) -> str:
    timeout = int(os.getenv("TASK_TEST_TIMEOUT_SECONDS", "180"))
    completed = subprocess.run(
        ["python", "-m", "pytest", "-q"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    return f"pytest exit code: {completed.returncode}\n{output[-6000:]}"


def execute_task(task: str, root: Path, scout_runner=None) -> TaskResult:
    """Execute only allow-listed, non-destructive digital actions."""
    plan = plan_task(task)
    if plan.intent is TaskIntent.UNKNOWN and not plan.actions:
        return TaskResult(plan, "ignored", plan.explanation)

    outputs: list[str] = []
    for action in plan.actions:
        if action == "inspect":
            outputs.append(_inspect(root))
        elif action == "test":
            try:
                outputs.append(_test(root))
            except subprocess.TimeoutExpired:
                outputs.append("pytest timed out; no changes were made.")
        elif action == "audit":
            if scout_runner is None:
                outputs.append("Audit runner is not available in this execution context.")
            else:
                scout_runner()
                outputs.append("Existing autonomous scout audit completed.")
        elif action == "discover":
            if scout_runner is None:
                outputs.append("Discovery runner is not available in this execution context.")
            else:
                scout_runner()
                outputs.append("Existing free-first discovery pipeline completed.")

    if plan.requires_approval:
        outputs.append("WRITE/DEPLOY STEP BLOCKED: approval is required before modifying source, merging, or deploying.")
        status = "approval_required"
    else:
        status = "completed"
    return TaskResult(plan, status, "\n\n".join(outputs))
