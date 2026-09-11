from __future__ import annotations

from .task_plan_models import TaskIntent


def decompose_task(task: str, intent: TaskIntent) -> tuple[str, ...]:
    """Produce bounded, auditable step descriptions without selecting or authorizing tools."""
    if intent is TaskIntent.UNKNOWN:
        return ("Clarify or inspect the request before selecting an executable capability.",)
    if intent is TaskIntent.RESEARCH:
        return ("Identify the requested information source.", "Retrieve and analyze the requested information.", "Verify the result and record sources.")
    if intent is TaskIntent.INSPECT:
        return ("Inspect the relevant project or workspace state.", "Evaluate deterministic findings.", "Verify and summarize the result.")
    if intent is TaskIntent.TEST:
        return ("Inspect the testable project state.", "Run the registered testing capability.", "Verify test status and summarize failures.")
    if intent in {TaskIntent.CHANGE, TaskIntent.IMPROVE}:
        return ("Inspect the current state and constraints.", "Prepare a bounded change proposal.", "Require the existing approval boundary before any write.", "Verify the approved result.")
    if intent is TaskIntent.AUTOMATE:
        return ("Inspect the requested automation target and constraints.", "Select only registered capabilities.", "Require the applicable approval, sandbox, lifecycle, and audit boundary.", "Verify the resulting action state.")
    return (f"Process task: {task}",)
