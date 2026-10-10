from __future__ import annotations

import re

from .task_plan_models import TaskIntent


_NEGATED_DIRECTIVE = re.compile(
    r"\b(?:do\s+not|don't|dont|never|avoid)\s+"
    r"(?:use|using|access|accessing|open|opening|execute|executing|run|running|"
    r"invoke|invoking|call|calling|touch|touching|modify|modifying|change|changing|"
    r"write|writing|send|sending|browse|browsing|search|searching|visit|visiting|"
    r"navigate|navigating|connect|connecting|interact|interacting)"
    r"(?:\s+(?:with|to))?\b"
    r"[^.!?;]*(?:[.!?;]|$)",
    re.IGNORECASE,
)

_NEGATED_WITHOUT_DIRECTIVE = re.compile(
    r"\bwithout\s+(?:using|accessing|opening|executing|running|invoking|calling|"
    r"touching|modifying|changing|writing|sending|browsing|searching|visiting|"
    r"navigating|connecting|interacting)\b"
    r"[^.!?;]*(?:[.!?;]|$)",
    re.IGNORECASE,
)


def positive_task_text(task: str) -> str:
    """Remove explicit negative action constraints before keyword-based routing.

    The original task remains intact for audit and execution. This normalized
    view is only for choosing an intent/specialist/tool, so a constraint such
    as "do not use email" cannot accidentally select the email capability.
    """
    text = " ".join(str(task).split()).lower()
    while True:
        cleaned = _NEGATED_DIRECTIVE.sub(" ", text)
        cleaned = _NEGATED_WITHOUT_DIRECTIVE.sub(" ", cleaned)
        cleaned = " ".join(cleaned.split())
        if cleaned == text:
            return cleaned
        text = cleaned


def classify_intent(task: str) -> TaskIntent:
    text = positive_task_text(task)
    if not text:
        return TaskIntent.UNKNOWN
    if any(term in text for term in ("use computer", "use the computer", "control computer", "control the computer", "operate the computer", "desktop", "gui", "double click", "scroll down", "scroll up", "drag and drop", "press enter", "computer task")):
        return TaskIntent.COMPUTER
    if any(term in text for term in ("email", "gmail", "mailbox", "message thread", "email thread", "send email", "draft email")):
        return TaskIntent.EMAIL
    if any(term in text for term in ("calendar", "calendars", "event", "events", "meeting", "meetings", "appointment", "schedule a meeting", "free time", "available time")):
        return TaskIntent.CALENDAR
    if any(term in text for term in ("automate", "automation", "schedule")):
        return TaskIntent.AUTOMATE
    if any(term in text for term in ("research", "search web", "look up", "find information", "investigate", "browse", "browser", "web page", "read this page", "open this page")) or "http://" in text or "https://" in text:
        return TaskIntent.RESEARCH
    if any(term in text for term in ("file", "files", "workspace", "directory", "folder", "read file", "write file", "transform file", "run command", "shell command", "py_compile", "compile python")):
        return TaskIntent.WORKSPACE
    if any(term in text for term in ("fix", "change", "edit", "modify", "update code", "repair")):
        return TaskIntent.CHANGE
    if any(term in text for term in ("test", "pytest", "run tests", "validate")):
        return TaskIntent.TEST
    if any(term in text for term in ("improve", "implement", "add feature", "build", "refactor")):
        return TaskIntent.IMPROVE
    if any(term in text for term in ("inspect", "analyze", "analyse", "audit", "review")):
        return TaskIntent.INSPECT
    return TaskIntent.UNKNOWN
