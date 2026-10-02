from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .capability_policy import Capability
from .specialist_router import SpecialistRole, choose_specialist
from .task_planner import candidate_tool_names, default_grants_for_task
from .tool_registry import ToolRegistry, REGISTRY


@dataclass(frozen=True)
class SpecialistProfile:
    role: SpecialistRole
    allowed_capabilities: frozenset[Capability]
    description: str


_COMMON = frozenset(
    {
        Capability.INSPECT,
        Capability.TEST,
        Capability.LINT,
        Capability.METRICS,
        Capability.READ_FILE,
        Capability.FILES_WORKSPACE,
        Capability.DOCUMENTS,
    }
)

PROFILES: dict[SpecialistRole, SpecialistProfile] = {
    SpecialistRole.PLANNER: SpecialistProfile(
        SpecialistRole.PLANNER,
        _COMMON | frozenset({Capability.WEB_RESEARCH}),
        "Plans and inspects bounded work; cannot widen permissions.",
    ),
    SpecialistRole.RESEARCH: SpecialistProfile(
        SpecialistRole.RESEARCH,
        _COMMON
        | frozenset(
            {
                Capability.WEB_RESEARCH,
                Capability.BROWSER,
                Capability.BENCHMARK,
                Capability.REST_API,
            }
        ),
        "Researches and verifies information through bounded read-only sources.",
    ),
    SpecialistRole.CODING: SpecialistProfile(
        SpecialistRole.CODING,
        _COMMON | frozenset({Capability.WORKSPACE_SHELL}),
        "Inspects and validates software changes without autonomous source writes.",
    ),
    SpecialistRole.BROWSER: SpecialistProfile(
        SpecialistRole.BROWSER,
        frozenset({Capability.BROWSER, Capability.WEB_RESEARCH, Capability.DOCUMENTS, Capability.READ_FILE}),
        "Operates bounded browser/read workflows.",
    ),
    SpecialistRole.COMPUTER: SpecialistProfile(
        SpecialistRole.COMPUTER,
        frozenset({Capability.COMPUTER, Capability.APPLICATION, Capability.DOCUMENTS, Capability.READ_FILE}),
        "Operates bounded desktop/application workflows.",
    ),
    SpecialistRole.DOCUMENTS: SpecialistProfile(
        SpecialistRole.DOCUMENTS,
        frozenset({Capability.DOCUMENTS, Capability.FILES_WORKSPACE, Capability.READ_FILE, Capability.APPLICATION}),
        "Inspects and transforms bounded document workflows.",
    ),
    SpecialistRole.COMMUNICATION: SpecialistProfile(
        SpecialistRole.COMMUNICATION,
        frozenset({Capability.EMAIL, Capability.CALENDAR, Capability.COMMUNICATION, Capability.BROWSER, Capability.WEB_RESEARCH}),
        "Coordinates bounded communication and scheduling workflows.",
    ),
    SpecialistRole.GENERAL: SpecialistProfile(
        SpecialistRole.GENERAL,
        frozenset(Capability),
        "General specialist routing; the task planner still narrows to selected tools.",
    ),
}


def profile_for(role: SpecialistRole | str) -> SpecialistProfile:
    resolved = role if isinstance(role, SpecialistRole) else SpecialistRole(str(role))
    return PROFILES.get(resolved, PROFILES[SpecialistRole.GENERAL])


def specialist_grants(
    task: str,
    role: SpecialistRole | str | None = None,
    *,
    registry: ToolRegistry = REGISTRY,
    include_approval_tools: bool = False,
) -> tuple[Capability, ...]:
    decision = choose_specialist(task) if role is None else None
    resolved = decision.role if decision is not None else (
        role if isinstance(role, SpecialistRole) else SpecialistRole(str(role))
    )
    profile = profile_for(resolved)
    if not include_approval_tools:
        selected = default_grants_for_task(task, registry)
        return tuple(capability for capability in selected if capability in profile.allowed_capabilities)

    values: list[Capability] = []
    seen: set[Capability] = set()
    for tool_name in candidate_tool_names(task, registry):
        tool = registry.get(tool_name)
        if tool is None:
            continue
        capability = Capability(tool.capability)
        if capability in profile.allowed_capabilities and capability not in seen:
            seen.add(capability)
            values.append(capability)
    return tuple(values)


def specialist_for_task(task: str) -> tuple[SpecialistRole, tuple[Capability, ...]]:
    decision = choose_specialist(task)
    return decision.role, specialist_grants(task, decision.role)


__all__ = [
    "PROFILES",
    "SpecialistProfile",
    "profile_for",
    "specialist_for_task",
    "specialist_grants",
]
