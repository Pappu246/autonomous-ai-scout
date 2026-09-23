from __future__ import annotations

import re

from .task_plan_models import TaskIntent


def _positive_clause_contains(text: str, *terms: str) -> bool:
    clauses = re.split(r"[.;!?\n]+", text)
    negative_markers = ("do not", "don't", "never", "without")
    return any(
        any(term in clause for term in terms)
        and not any(marker in clause for marker in negative_markers)
        for clause in clauses
    )


def classify_intent(task: str) -> TaskIntent:
    text = " ".join(task.strip().split()).lower()
    if not text:
        return TaskIntent.UNKNOWN
    if any(term in text for term in ("email", "gmail", "mailbox", "message thread", "email thread", "send email", "draft email")):
        return TaskIntent.EMAIL
    if any(term in text for term in ("calendar", "calendars", "event", "events", "meeting", "meetings", "appointment", "schedule a meeting", "free time", "available time")):
        return TaskIntent.CALENDAR
    if any(term in text for term in ("automate", "automation", "schedule")):
        return TaskIntent.AUTOMATE
    # Positive source-change requests outrank incidental mentions of tests/files.
    if _positive_clause_contains(text, "fix", "change", "edit", "modify", "update code", "repair"):
        return TaskIntent.CHANGE
    if _positive_clause_contains(text, "add tests", "add test", "write tests", "write test", "test suite", "run tests", "run the tests", "pytest", "unit tests", "integration tests"):
        return TaskIntent.TEST
    if _positive_clause_contains(text, "improve", "implement", "add feature", "build", "refactor"):
        return TaskIntent.IMPROVE
    if "repository workspace" in text or "local workspace" in text:
        return TaskIntent.WORKSPACE
    if any(term in text for term in ("research", "search web", "look up", "find information", "investigate", "browse", "browser", "web page", "read this page", "open this page")) or "http://" in text or "https://" in text:
        return TaskIntent.RESEARCH

    # Inspection/audit/review tasks must not become workspace tasks merely
    # because they mention files, especially in a negative safety clause such
    # as "do not modify any files". An explicit workspace target still takes
    # the workspace path.
    if any(term in text for term in ("inspect", "analyze", "analyse", "audit", "review")):
        explicit_workspace_target = _positive_clause_contains(
            text, "workspace", "local workspace", "directory", "folder"
        )
        workspace_action = _positive_clause_contains(
            text,
            "read file", "read the file", "open file", "list files",
            "list directory", "list folder", "write file", "create file",
            "save file", "transform file", "modify file", "replace in file",
            "run command", "shell command", "py_compile", "compile python",
        )
        if not explicit_workspace_target and not workspace_action:
            return TaskIntent.INSPECT

    if any(term in text for term in ("file", "files", "workspace", "directory", "folder", "read file", "write file", "transform file", "run command", "shell command", "py_compile", "compile python")):
        return TaskIntent.WORKSPACE

    if any(term in text for term in ("inspect", "analyze", "analyse", "audit", "review")):
        return TaskIntent.INSPECT
    return TaskIntent.UNKNOWN
