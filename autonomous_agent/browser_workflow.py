from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .browser_connector import ControlledBrowser, BrowserResult
from .browser_tooling import execute_browser_tool

MAX_BROWSER_STEPS = 12


@dataclass(frozen=True)
class BrowserAction:
    operation: str
    url: str
    selector: str = ""
    fields: tuple[str, ...] = ()
    timeout_seconds: int = 20


@dataclass(frozen=True)
class BrowserWorkflowResult:
    success: bool
    completed_steps: int
    results: tuple[BrowserResult, ...]
    failed_step: int | None
    reason: str


class BrowserWorkflow:
    """Execute a bounded browser workflow through ControlledBrowser only."""

    def __init__(self, browser: ControlledBrowser) -> None:
        self.browser = browser

    def run(self, actions: Iterable[BrowserAction]) -> BrowserWorkflowResult:
        sequence = tuple(actions)
        if not sequence:
            return BrowserWorkflowResult(False, 0, (), None, "browser workflow must contain at least one action")
        if len(sequence) > MAX_BROWSER_STEPS:
            return BrowserWorkflowResult(False, 0, (), None, f"browser workflow exceeds step limit of {MAX_BROWSER_STEPS}")

        results: list[BrowserResult] = []
        for index, action in enumerate(sequence):
            request: Mapping[str, Any] = {
                "operation": action.operation,
                "url": action.url,
                "selector": action.selector,
                "fields": list(action.fields),
                "timeout_seconds": action.timeout_seconds,
            }
            try:
                raw = execute_browser_tool(self.browser, request)
                result = BrowserResult(
                    str(raw.get("action", action.operation)),
                    str(raw.get("url", action.url)),
                    str(raw.get("title", "")),
                    str(raw.get("text", "")),
                    tuple(dict(link) for link in raw.get("links", ())),
                    raw.get("status_code") if isinstance(raw.get("status_code"), int) else None,
                    str(raw.get("verification_status", "verified")),
                )
            except Exception as exc:
                return BrowserWorkflowResult(False, len(results), tuple(results), index, f"browser action failed: {type(exc).__name__}")

            results.append(result)
            if result.verification_status != "verified":
                return BrowserWorkflowResult(False, len(results), tuple(results), index, "browser action verification failed")

        return BrowserWorkflowResult(True, len(results), tuple(results), None, "browser workflow completed and every action was verified")


__all__ = ["BrowserAction", "BrowserWorkflow", "BrowserWorkflowResult", "MAX_BROWSER_STEPS"]
