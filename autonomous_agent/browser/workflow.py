"""Bounded multi-step browser workflow.

Encodes the canonical browser loop:

    observe -> target -> execute -> observe -> verify -> next step

with hard bounds so no workflow can loop forever. Every step is bounded by the
session action budget and by an explicit maximum step count. A step that cannot
be verified stops the workflow (fail closed); the workflow never fabricates a
VERIFIED result and never repeats a completed mutation on resume (replay
protection lives on the connector).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import MAX_WORKFLOW_STEPS, BrowserError


@dataclass(frozen=True)
class WorkflowStep:
    """One declarative step in a bounded browser workflow.

    ``operation`` is one of the connector's capability method names. ``target``
    is an optional already-resolved semantic target; when omitted, the step's
    query parameters (role/accessible_name/visible_text/selector) are resolved
    against the current page at execution time.
    """

    operation: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowStepResult:
    index: int
    operation: str
    ok: bool
    evidence: Mapping[str, Any] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class WorkflowResult:
    success: bool
    completed_steps: int
    results: tuple[WorkflowStepResult, ...]
    failed_step: int | None
    reason: str


#: Operations that must never be driven by a workflow step (defence in depth).
_FORBIDDEN_OPERATIONS = frozenset(
    {"eval", "evaluate", "execute_script", "run_script", "cdp", "debugger", "screenshot_raw"}
)


class BoundedBrowserWorkflow:
    """Execute a bounded sequence of browser steps with verification per step."""

    def __init__(self, connector, *, max_steps: int = MAX_WORKFLOW_STEPS) -> None:
        self._connector = connector
        self._max_steps = max(1, min(int(max_steps), MAX_WORKFLOW_STEPS))

    def run(self, steps) -> WorkflowResult:
        sequence = tuple(steps)
        if not sequence:
            return WorkflowResult(False, 0, (), None, "workflow must contain at least one step")
        if len(sequence) > self._max_steps:
            return WorkflowResult(
                False, 0, (), None,
                f"workflow exceeds the bounded step limit of {self._max_steps}",
            )

        results: list[WorkflowStepResult] = []
        for index, step in enumerate(sequence):
            op = str(step.operation).strip().lower()
            if op in _FORBIDDEN_OPERATIONS:
                return WorkflowResult(
                    False, len(results), tuple(results), index,
                    f"workflow operation is forbidden: {op}",
                )
            method = getattr(self._connector, op, None)
            if method is None or op.startswith("_") or not callable(method):
                return WorkflowResult(
                    False, len(results), tuple(results), index,
                    f"workflow operation is not a bounded browser capability: {op}",
                )
            try:
                evidence = method(**dict(step.arguments))
            except BrowserError as exc:
                return WorkflowResult(
                    False, len(results), tuple(results), index,
                    f"step {index} ({op}) failed closed: {exc}",
                )
            except Exception as exc:  # noqa: BLE001 - any failure stops the workflow
                return WorkflowResult(
                    False, len(results), tuple(results), index,
                    f"step {index} ({op}) error: {type(exc).__name__}",
                )

            ok = self._step_verified(op, evidence)
            results.append(WorkflowStepResult(index, op, ok, dict(evidence or {})))
            if not ok:
                return WorkflowResult(
                    False, len(results), tuple(results), index,
                    f"step {index} ({op}) produced no observable post-condition",
                )
            # The session action budget is enforced by the connector on the next
            # consume; when it is exhausted the following step fails closed rather
            # than looping. There is no unbounded retry anywhere in this loop.

        return WorkflowResult(True, len(results), tuple(results), None, "workflow completed and verified")


# --------------------------------------------------------------------------
# Capability declarations are in digital/builtins.py; this table is only the
# workflow's per-step observable-post-condition check (it never fabricates a
# success the connector did not report).
# --------------------------------------------------------------------------
def _step_verified(self, op: str, evidence: Mapping[str, Any]) -> bool:
    if not isinstance(evidence, Mapping) or not evidence:
        return False
    markers = {
        "session_open": ("opened",),
        "navigate": ("navigated",),
        "back": ("url",),
        "forward": ("url",),
        "reload": ("url",),
        "page_observe": ("url",),
        "element_find": ("found",),
        "element_click": ("clicked",),
        "element_type": ("typed",),
        "element_select": ("selected",),
        "download_start": ("downloaded",),
        "file_extract": ("extracted",),
        "open": ("action",),
        "click": ("action",),
        "extract": ("action",),
    }
    expected = markers.get(op)
    if expected is None:
        # Unknown-but-allowed op: require *some* truthy evidence value.
        return any(v not in (None, "", [], {}, False) for v in evidence.values())
    return all(bool(evidence.get(key)) for key in expected)


# Bind as a method (kept module-level for readability/testability).
BoundedBrowserWorkflow._step_verified = _step_verified


__all__ = ["BoundedBrowserWorkflow", "WorkflowResult", "WorkflowStep", "WorkflowStepResult"]
