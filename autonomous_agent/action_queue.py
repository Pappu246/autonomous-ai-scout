from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActionStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ActionProposal:
    task: str
    steps: tuple[str, ...]
    requires_approval: bool
    status: ActionStatus
    reason: str


def _sensitive(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "write", "modify", "change code", "edit", "delete", "remove",
        "merge", "deploy", "release", "credential", "secret", "token",
        "password", "billing", "payment", "production", "destructive",
    )
    return any(marker in lowered for marker in markers)


def build_action_proposal(task: str, steps: tuple[str, ...], llm_requires_approval: bool = False) -> ActionProposal:
    """Create a conservative action boundary; model output can only increase risk, never reduce it."""
    task_risky = _sensitive(task)
    steps_risky = any(_sensitive(step) for step in steps)
    requires = bool(llm_requires_approval or task_risky or steps_risky)
    if requires:
        return ActionProposal(
            task=task,
            steps=steps[:12],
            requires_approval=True,
            status=ActionStatus.PROPOSED,
            reason="Approval is required because the task or proposed steps cross a sensitive action boundary.",
        )
    return ActionProposal(
        task=task,
        steps=steps[:12],
        requires_approval=False,
        status=ActionStatus.COMPLETED,
        reason="Only bounded non-sensitive actions were proposed.",
    )
