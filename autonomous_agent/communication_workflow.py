from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .capability_policy import CapabilityDecision
from .digital_tool import ToolInvocation, UniversalDigitalToolLayer


@dataclass(frozen=True)
class CommunicationStep:
    tool_name: str
    purpose: str
    requires_approval: bool


@dataclass(frozen=True)
class CommunicationPlan:
    objective: str
    steps: tuple[CommunicationStep, ...]

    @property
    def approval_required(self) -> bool:
        return any(step.requires_approval for step in self.steps)

    @property
    @property
    def safe_read_steps(self) -> tuple[str, ...]:
        return tuple(step.tool_name for step in self.steps if not step.requires_approval)


class CommunicationWorkflow:
    """Coordinate Gmail/Calendar actions without crossing their approval gates."""

    def __init__(self, tools: UniversalDigitalToolLayer | None = None) -> None:
        self.tools = tools or UniversalDigitalToolLayer()

    def plan_meeting_coordination(
        self,
        objective: str,
        *,
        include_email_search: bool = True,
        draft_email: bool = False,
        create_event: bool = False,
    ) -> CommunicationPlan:
        steps: list[CommunicationStep] = []
        if include_email_search:
            steps.append(CommunicationStep("email.search", "find the relevant conversation", False))
        steps.append(CommunicationStep("calendar.find_free_time", "find compatible availability", False))
        if draft_email:
            steps.append(CommunicationStep("email.draft", "prepare an unsent coordination message", True))
        if create_event:
            steps.append(CommunicationStep("calendar.event.create", "create the agreed calendar event", True))
        return CommunicationPlan(" ".join(objective.strip().split()), tuple(steps))

    def authorization_report(
        self,
        plan: CommunicationPlan,
        *,
        granted: Iterable[str] = (),
        explicitly_approved: bool = False,
    ) -> tuple[tuple[str, CapabilityDecision], ...]:
        report = []
        for step in plan.steps:
            decision = self.tools.authorize(
                step.tool_name,
                granted,
                explicitly_approved=explicitly_approved,
                sandbox_available=True,
                audit_available=True,
            )
            report.append((step.tool_name, decision))
        return tuple(report)

    def send_is_never_implied(self, plan: CommunicationPlan) -> bool:
        return all(step.tool_name != "email.send" for step in plan.steps)


__all__ = ["CommunicationPlan", "CommunicationStep", "CommunicationWorkflow"]
