from __future__ import annotations

from dataclasses import dataclass

from .task_plan_models import TaskIntent
from .tool_registry import REGISTRY, ToolRegistry


@dataclass(frozen=True)
class ToolSelection:
    task: str
    intent: TaskIntent
    candidate_names: tuple[str, ...]
    tool_names: tuple[str, ...]
    reason: str

    @property
    def missing_tools(self) -> tuple[str, ...]:
        return tuple(name for name in self.candidate_names if name not in self.tool_names)


class DynamicToolRouter:
    """Select the narrowest registered tool set implied by one task request."""

    def __init__(self, registry: ToolRegistry = REGISTRY) -> None:
        self._registry = registry

    @staticmethod
    def _calendar_tools(task: str) -> tuple[str, ...]:
        text = task.lower()
        if any(x in text for x in ("cancel", "delete event")):
            return ("calendar.event.cancel",)
        if any(x in text for x in ("update", "reschedule", "move meeting", "modify event")):
            return ("calendar.event.update",)
        if any(x in text for x in ("create", "book", "add event", "schedule a meeting", "schedule meeting")):
            return ("calendar.event.create",)
        if any(x in text for x in ("free time", "available time", "availability")):
            return ("calendar.find_free_time",)
        if any(x in text for x in ("read event", "event details", "get event")):
            return ("calendar.read",)
        return ("calendar.list",)

    @staticmethod
    def _workspace_tools(task: str) -> tuple[str, ...]:
        text = task.lower()
        if any(x in text for x in ("transform file", "replace in file", "modify file")):
            return ("filesystem.transform",)
        if any(x in text for x in ("write file", "create file", "save file")):
            return ("filesystem.write",)
        if any(x in text for x in ("list files", "list directory", "list folder", "directory", "folder")):
            return ("filesystem.list",)
        return ("filesystem.read",)

    @staticmethod
    def _email_tools(task: str) -> tuple[str, ...]:
        text = task.lower()
        if any(x in text for x in ("send email", "send a mail", "reply email")):
            return ("email.send",)
        if any(x in text for x in ("draft email", "draft an email", "draft a mail", "draft the email", "compose email", "compose an email", "compose a mail", "compose the email")):
            return ("email.draft",)
        if "thread" in text:
            return ("email.thread",)
        if any(x in text for x in ("read email", "read message", "open email")):
            return ("email.read",)
        return ("email.search",)

    @staticmethod
    def _browser_tools(task: str) -> tuple[str, ...]:
        text = task.lower()
        names: list[str] = []
        if any(x in text for x in ("open", "visit", "navigate", "go to")):
            names.append("browser.open")
        if any(x in text for x in ("click", "press", "select")):
            names.append("browser.click")
        if any(x in text for x in ("extract", "read page", "collect text")):
            names.append("browser.extract")
        return tuple(dict.fromkeys(names)) or ("browser.open",)

    @staticmethod
    def _research_tools(task: str) -> tuple[str, ...]:
        text = task.lower()
        if "browser" in text or "browse" in text or "click" in text:
            return DynamicToolRouter._browser_tools(task)
        if any(x in text for x in ("http://", "https://", "read this page", "open this page")):
            return ("web.read",)
        names: list[str] = ["web.search"]
        if any(x in text for x in ("compare", "comparison", "versus", " vs ")):
            names.append("web.compare")
        if any(x in text for x in ("extract", "key facts", "specific fields")):
            names.append("web.extract")
        if not any(x in text for x in ("compare", "comparison", "versus", " vs ", "extract", "key facts", "specific fields")):
            names.extend(("web.read", "web.extract", "web.compare"))
        return tuple(dict.fromkeys(names))

    def select_names(self, task: str, intent: TaskIntent | None = None) -> ToolSelection:
        raw = " ".join(task.strip().split())
        resolved = intent or TaskIntent.UNKNOWN
        if isinstance(resolved, str):
            try:
                resolved = TaskIntent(resolved)
            except ValueError:
                resolved = TaskIntent.UNKNOWN
        if intent is None:
            from .task_intent import classify_intent
            resolved = classify_intent(raw)

        if resolved is TaskIntent.CALENDAR:
            names = self._calendar_tools(raw)
        elif resolved is TaskIntent.EMAIL:
            names = self._email_tools(raw)
        elif resolved is TaskIntent.WORKSPACE:
            names = self._workspace_tools(raw)
        elif resolved is TaskIntent.RESEARCH:
            names = self._research_tools(raw)
        elif resolved is TaskIntent.AUTOMATE:
            names = self._browser_tools(raw) if any(x in raw.lower() for x in ("browser", "web", "click", "navigate")) else ()
        elif resolved is TaskIntent.TEST:
            names = ("github.inspect", "lint.run") if "lint" in raw.lower() else ("github.inspect", "tests.run")
        elif resolved in {TaskIntent.CHANGE, TaskIntent.IMPROVE}:
            names = ("github.inspect", "tests.run", "github.change")
        elif resolved is TaskIntent.INSPECT:
            names = ("github.inspect",)
        elif resolved is TaskIntent.UNKNOWN:
            names = ("github.inspect",)
        else:
            names = ()

        available = tuple(name for name in names if self._registry.get(name) is not None)
        missing = tuple(name for name in names if self._registry.get(name) is None)
        if missing:
            reason = f"Selected registered capabilities: {', '.join(available) or 'none'}; missing required tools: {', '.join(missing)}."
        elif available:
            reason = f"Selected the narrowest registered tool set for {resolved.value} from the task request."
        else:
            reason = "No executable tool mapping is safe to infer from the request."
        return ToolSelection(raw, resolved, tuple(names), available, reason)

    def select(self, task: str, intent: TaskIntent | None = None) -> tuple[str, ...]:
        return self.select_names(task, intent).tool_names


__all__ = ["DynamicToolRouter", "ToolSelection"]
