"""User goal and intent understanding.

This module separates *what the user wants* from *how it is executed*. A
:class:`DigitalGoal` carries the user's natural-language objective; an
:class:`IntentProfile` records what the system understood about it, derived
entirely from the capability catalog rather than from hard-coded domain logic.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..prompt_injection_guard import TrustLevel
from .catalog import CapabilityCatalog, RoutingResult
from .contract import CapabilityError
from .domains import CapabilityDomain


MAX_GOAL_LENGTH = 4096

_CREDENTIAL_MATERIAL = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|"
    r"private[_-]?key|client[_-]?secret)\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class DigitalGoal:
    """One bounded user objective in natural language."""

    text: str
    project: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    origin_trust: TrustLevel = TrustLevel.USER

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise CapabilityError("a digital goal requires non-empty natural language")
        if len(self.text) > MAX_GOAL_LENGTH:
            raise CapabilityError("digital goal exceeds the bounded length limit")
        if not isinstance(self.context, Mapping):
            raise CapabilityError("digital goal context must be an object")
        # Credentials must never ride along inside a goal: they belong in the
        # credential broker and are addressed only by credref.
        for value in (self.text, *(str(item) for item in self.context.values())):
            if _CREDENTIAL_MATERIAL.search(value):
                raise CapabilityError("digital goal must not contain credential material")

    @property
    def normalized(self) -> str:
        return " ".join(self.text.strip().split())

    @property
    def digest(self) -> str:
        payload = {
            "text": self.normalized,
            "project": self.project,
            "context": dict(self.context),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class IntentProfile:
    """What the system understood about a goal before any planning."""

    goal: DigitalGoal
    routing: RoutingResult
    domains: tuple[CapabilityDomain, ...]
    reserved_domains: tuple[CapabilityDomain, ...]
    selected: tuple[str, ...]
    understood: bool
    summary: str

    @property
    def requires_unregistered_capability(self) -> bool:
        return bool(self.reserved_domains)

    def safe_dict(self) -> dict[str, object]:
        return {
            "goal_digest": self.goal.digest,
            "text": self.goal.normalized,
            "project": self.goal.project,
            "domains": tuple(item.value for item in self.domains),
            "reserved_domains": tuple(item.value for item in self.reserved_domains),
            "selected": tuple(self.selected),
            "understood": self.understood,
            "summary": self.summary,
        }


def understand_goal(goal: DigitalGoal | str, catalog: CapabilityCatalog) -> IntentProfile:
    """Turn a user goal into a capability-domain understanding.

    Understanding never authorizes and never executes. It only reports which
    declared domains the goal touches and which registered capabilities could
    serve it.
    """
    resolved = goal if isinstance(goal, DigitalGoal) else DigitalGoal(str(goal))
    routing = catalog.route(resolved.normalized)
    if routing.reserved_required:
        understood = False
        summary = (
            "goal needs capability domains that are declared but not yet "
            f"registered: {', '.join(item.value for item in routing.reserved_required)}"
        )
    elif routing.selected:
        understood = True
        summary = (
            "goal maps to registered capabilities: " + ", ".join(routing.selected)
        )
    else:
        understood = False
        summary = routing.reason
    return IntentProfile(
        goal=resolved,
        routing=routing,
        domains=routing.matched_domains,
        reserved_domains=routing.reserved_required,
        selected=routing.selected,
        understood=understood,
        summary=summary,
    )


__all__ = [
    "MAX_GOAL_LENGTH",
    "DigitalGoal",
    "IntentProfile",
    "understand_goal",
]
