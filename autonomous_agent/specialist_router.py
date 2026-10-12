from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .task_intent import positive_task_text


class SpecialistRole(str, Enum):
    PLANNER = "planner"
    RESEARCH = "research"
    CODING = "coding"
    BROWSER = "browser"
    COMPUTER = "computer"
    DOCUMENTS = "documents"
    COMMUNICATION = "communication"
    GENERAL = "general"


@dataclass(frozen=True)
class RoleDecision:
    role: SpecialistRole
    confidence: float
    reasons: tuple[str, ...]


_RULES: tuple[tuple[SpecialistRole, tuple[str, ...], str], ...] = (
    (
        SpecialistRole.CODING,
        ("code", "coding", "bug", "debug", "test", "pytest", "repository", "repo", "github", "refactor"),
        "software-engineering language",
    ),
    (
        SpecialistRole.RESEARCH,
        ("research", "find", "compare", "source", "sources", "investigate", "latest", "report", "audit", "benchmark"),
        "research or evidence language",
    ),
    (
        SpecialistRole.BROWSER,
        ("browser", "website", "webpage", "site", "click", "login", "open page", "navigate"),
        "browser interaction language",
    ),
    (
        SpecialistRole.COMPUTER,
        ("computer", "desktop", "window", "mouse", "keyboard", "screen", "chrome", "application", "app"),
        "desktop-control language",
    ),
    (
        SpecialistRole.DOCUMENTS,
        ("pdf", "document", "docx", "spreadsheet", "excel", "table", "document", "extract text"),
        "document-processing language",
    ),
    (
        SpecialistRole.COMMUNICATION,
        ("email", "gmail", "mail", "calendar", "meeting", "schedule", "invite", "send message"),
        "communication or scheduling language",
    ),
)


def choose_specialist(task: str) -> RoleDecision:
    normalized = positive_task_text(task)
    scores: list[tuple[int, SpecialistRole, tuple[str, ...], str]] = []
    for role, keywords, reason in _RULES:
        hits = tuple(keyword for keyword in keywords if keyword in normalized)
        if hits:
            scores.append((len(hits), role, hits, reason))

    if not scores:
        return RoleDecision(SpecialistRole.GENERAL, 0.25, ("no specialist-specific signal",))

    scores.sort(key=lambda item: (-item[0], item[1].value))
    best_count, role, hits, reason = scores[0]
    confidence = min(0.98, 0.45 + best_count * 0.12)
    return RoleDecision(
        role,
        round(confidence, 3),
        (reason, "matched: " + ", ".join(hits[:6])),
    )


def specialist_roles() -> tuple[SpecialistRole, ...]:
    return tuple(SpecialistRole)


__all__ = ["RoleDecision", "SpecialistRole", "choose_specialist", "specialist_roles"]
