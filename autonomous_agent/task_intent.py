from __future__ import annotations

from .task_plan_models import TaskIntent


def classify_intent(task: str) -> TaskIntent:
    """Classify intent conservatively; ambiguous requests remain unknown."""
    text = " ".join(task.strip().split()).lower()
    if not text:
        return TaskIntent.UNKNOWN
    if any(term in text for term in ("research", "search web", "look up", "find information", "investigate")):
        return TaskIntent.RESEARCH
    if any(term in text for term in ("test", "pytest", "run tests", "validate")):
        return TaskIntent.TEST
    if any(term in text for term in ("inspect", "analyze", "analyse", "audit", "review")):
        return TaskIntent.INSPECT
    if any(term in text for term in ("automate", "automation", "schedule", "browser")):
        return TaskIntent.AUTOMATE
    if any(term in text for term in ("fix", "change", "edit", "modify", "update code")):
        return TaskIntent.CHANGE
    if any(term in text for term in ("improve", "implement", "add feature", "build", "refactor")):
        return TaskIntent.IMPROVE
    return TaskIntent.UNKNOWN
