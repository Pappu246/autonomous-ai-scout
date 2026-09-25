"""Capability selection and plan construction.

The planner contains **no domain vocabulary**. It consumes the catalog's
routing result and turns it into an ordered, authorization-annotated plan.
That is what allows a browser, computer or application adapter to be added
without editing this file.

Ordering is derived from each capability's declared ``stage`` (look first,
transform second, mutate last), so multi-capability plans read naturally and
reproducibly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..capability_policy import Capability
from ..prompt_injection_guard import TrustLevel
from .authorization import (
    CapabilityAuthorizationBroker,
    assert_no_escalation,
    autonomous_capabilities,
    narrow_grants,
)
from .catalog import CapabilityCatalog
from .contract import DigitalCapability
from .domains import CapabilityDomain
from .intent import DigitalGoal, IntentProfile, understand_goal


@dataclass(frozen=True)
class PlannedStep:
    """One planned capability invocation."""

    step_id: str
    capability_id: str
    tool_name: str
    domain: CapabilityDomain
    description: str
    risk: str
    read_write: str
    approval: str
    stage: int
    authorization: str
    reason: str

    @property
    def writes(self) -> bool:
        return self.read_write != "read_only"

    def safe_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "capability_id": self.capability_id,
            "tool_name": self.tool_name,
            "domain": self.domain.value,
            "description": self.description,
            "risk": self.risk,
            "read_write": self.read_write,
            "approval": self.approval,
            "authorization": self.authorization,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CapabilityPlan:
    """The canonical plan for one digital goal."""

    goal: DigitalGoal
    intent: IntentProfile
    steps: tuple[PlannedStep, ...]
    granted: tuple[Capability, ...]
    executable: bool
    reason: str
    requires_approval: bool
    plan_digest: str

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(step.capability_id for step in self.steps)

    @property
    def domains(self) -> tuple[CapabilityDomain, ...]:
        seen: list[CapabilityDomain] = []
        for step in self.steps:
            if step.domain not in seen:
                seen.append(step.domain)
        return tuple(seen)

    def safe_dict(self) -> dict[str, object]:
        return {
            "goal_digest": self.goal.digest,
            "text": self.goal.normalized,
            "project": self.goal.project,
            "executable": self.executable,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "granted": tuple(item.value for item in self.granted),
            "plan_digest": self.plan_digest,
            "steps": tuple(step.safe_dict() for step in self.steps),
            "intent": self.intent.safe_dict(),
        }


def _digest(goal: DigitalGoal, selected: Sequence[str], granted: Sequence[Capability]) -> str:
    payload = {
        "goal": goal.digest,
        "selected": tuple(selected),
        "granted": tuple(sorted(item.value for item in granted)),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class CapabilityPlanner:
    """Turn a user goal into a bounded, capability-typed plan."""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        *,
        authorization: CapabilityAuthorizationBroker | None = None,
    ) -> None:
        self._catalog = catalog
        self._authorization = authorization or CapabilityAuthorizationBroker(catalog)

    @property
    def catalog(self) -> CapabilityCatalog:
        return self._catalog

    def plan(
        self,
        goal: DigitalGoal | str,
        *,
        granted: Iterable[Capability | str] = (),
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
        origin_trust: TrustLevel = TrustLevel.USER,
    ) -> CapabilityPlan:
        """Produce the one canonical plan for a goal.

        The plan fails closed when the goal needs an unregistered domain, when a
        selected capability is missing, or when any step is not authorized.
        """
        resolved = goal if isinstance(goal, DigitalGoal) else DigitalGoal(str(goal))
        intent = understand_goal(resolved, self._catalog)

        if not intent.understood:
            return self._blocked(resolved, intent, intent.summary)

        capabilities: list[DigitalCapability] = []
        missing: list[str] = []
        for capability_id in intent.selected:
            capability = self._catalog.get(capability_id)
            if capability is None:
                missing.append(capability_id)
            else:
                capabilities.append(capability)
        if missing:
            return self._blocked(
                resolved,
                intent,
                "selected capabilities are not registered: " + ", ".join(missing),
            )

        # Grants are derived from what the selected capabilities need. Caller
        # grants are intersected, never unioned, so a goal cannot be escalated.
        requested = tuple(granted)
        effective_requested = requested or autonomous_capabilities(capabilities)
        effective = narrow_grants(capabilities, effective_requested)
        assert_no_escalation(capabilities, effective)

        authorization = self._authorization.evaluate(
            intent.selected,
            effective,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
            origin_trust=origin_trust,
        )

        steps: list[PlannedStep] = []
        for index, capability in enumerate(
            sorted(capabilities, key=lambda item: (item.descriptor.stage, item.descriptor.capability_id)),
            start=1,
        ):
            descriptor = capability.discover()
            verdict = next(
                (step for step in authorization.steps if step.capability_id == descriptor.capability_id),
                None,
            )
            allowed = bool(verdict and verdict.allowed)
            steps.append(
                PlannedStep(
                    step_id=f"step-{index}",
                    capability_id=descriptor.capability_id,
                    tool_name=descriptor.tool_name,
                    domain=descriptor.domain,
                    description=descriptor.description,
                    risk=descriptor.risk,
                    read_write=descriptor.read_write,
                    approval=descriptor.approval,
                    stage=descriptor.stage,
                    authorization="authorized" if allowed else "blocked",
                    reason=verdict.reason if verdict else "capability was not evaluated",
                )
            )

        if not authorization.allowed:
            return CapabilityPlan(
                goal=resolved,
                intent=intent,
                steps=tuple(steps),
                granted=effective,
                executable=False,
                reason=authorization.reason,
                requires_approval=authorization.requires_approval,
                plan_digest=_digest(resolved, intent.selected, effective),
            )

        return CapabilityPlan(
            goal=resolved,
            intent=intent,
            steps=tuple(steps),
            granted=effective,
            executable=True,
            reason="plan is executable; authorization is re-checked before every step",
            requires_approval=authorization.requires_approval,
            plan_digest=_digest(resolved, intent.selected, effective),
        )

    def _blocked(
        self, goal: DigitalGoal, intent: IntentProfile, reason: str
    ) -> CapabilityPlan:
        return CapabilityPlan(
            goal=goal,
            intent=intent,
            steps=(),
            granted=(),
            executable=False,
            reason=reason,
            requires_approval=False,
            plan_digest=_digest(goal, (), ()),
        )


__all__ = ["CapabilityPlan", "CapabilityPlanner", "PlannedStep"]
