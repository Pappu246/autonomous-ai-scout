"""Canonical capability domains for the universal bounded digital agent.

This module is the single place where the product's notion of "what kinds of
digital work exist" is declared. Domains are peers: GitHub is one domain among
many and is never treated as the definition of the product.

Two kinds of domain exist:

* ``registered`` -- at least one real capability is wired to it in this phase,
  so goals can be routed to it and executed through the existing safe
  execution boundary.
* ``reserved``  -- the domain is part of the product model but no capability
  exists yet (for example computer control in a later phase). Reserved domains
  are honest: the catalog reports them as unavailable and the planner fails
  closed instead of inventing a fake implementation.

Domain routing vocabulary lives here as *data* attached to each descriptor so
the planner can stay completely domain-agnostic. Adding a new domain never
requires editing the planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping


class CapabilityDomain(str, Enum):
    """Peer digital capability domains.

    ``value`` is the stable identifier used in capability ids
    (``"<domain>:<operation>"``), in the capability documentation model and in
    audit records. Values are part of the durable contract and must not be
    renamed casually.
    """

    COMPUTER = "computer"
    BROWSER = "browser"
    FILESYSTEM = "filesystem"
    OS_SHELL = "os_shell"
    WEB = "web"
    EMAIL = "email"
    CALENDAR = "calendar"
    GITHUB = "github"
    DOCUMENTS = "documents"
    APPLICATION = "application"
    TESTING = "testing"


class DomainPhase(str, Enum):
    """Whether a domain is usable now or intentionally deferred."""

    ACTIVE = "active"
    RESERVED = "reserved"


@dataclass(frozen=True)
class DomainDescriptor:
    """Declared, non-executable description of one capability domain."""

    domain: CapabilityDomain
    title: str
    description: str
    phase: DomainPhase
    signals: tuple[str, ...] = ()
    notes: str = ""

    @property
    def registered(self) -> bool:
        """True when this phase wires real capabilities for the domain."""
        return self.phase is DomainPhase.ACTIVE

    @property
    def reserved(self) -> bool:
        return self.phase is DomainPhase.RESERVED

    def safe_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain.value,
            "title": self.title,
            "description": self.description,
            "phase": self.phase.value,
            "signals": tuple(self.signals),
            "notes": self.notes,
        }


# Order matters: matching is resolved by descending ``priority`` so that a
# specific domain wins over a broad one (for example "open an application" is a
# computer task, not a browser task, even though both mention opening).
DOMAIN_DESCRIPTORS: tuple[DomainDescriptor, ...] = (
    DomainDescriptor(
        CapabilityDomain.COMPUTER,
        "Computer control",
        "Operate the local desktop: open applications, drive graphical "
        "interfaces, move the pointer and keyboard.",
        DomainPhase.ACTIVE,
        signals=(
            "open an application",
            "open application",
            "open the app",
            "launch application",
            "launch app",
            "desktop",
            "graphical",
            "gui",
            "screen",
            "window",
            "windows",
            "list windows",
            "list window",
            "active window",
            "focus window",
            "switch window",
            "all windows",
            "click on screen",
            "mouse click",
            "click mouse",
            "move mouse",
            "type text",
            "keyboard shortcut",
            "hotkey",
            "screenshot",
            "capture screen",
            "clipboard",
            "read clipboard",
            "write clipboard",
        ),
        notes="Bounded Windows desktop control domain.",
    ),
    DomainDescriptor(
        CapabilityDomain.APPLICATION,
        "Application adapters",
        "Drive a specific third-party application through a bounded adapter "
        "(editor, office suite, IDE).",
        DomainPhase.ACTIVE,
        signals=(
            "vs code",
            "visual studio code",
            "in excel",
            "in word",
            "photoshop",
            "figma",
            "slack app",
            "inside the app",
        ),
        notes="Active architecture domain; application adapters must be registered explicitly.",
    ),
    DomainDescriptor(
        CapabilityDomain.DOCUMENTS,
        "Document processing",
        "Inspect, transform and produce rich documents such as PDFs, "
        "spreadsheets and slide decks.",
        DomainPhase.ACTIVE,
        signals=(
            "read the pdf",
            "read pdf",
            "extract text from",
            "pdf content",
            "pdf pages",
            "merge pdf",
            "merge the pdf",
            "split pdf",
            "edit the pdf",
            "convert pdf",
            "fill in the form fields",
            "spreadsheet cells",
            "edit the spreadsheet",
            "slide deck content",
            "rewrite the document",
        ),
        notes="Active architecture domain. Signals are deliberately content-oriented: moving, "
        "renaming or listing files by extension is filesystem work and stays "
        "with the filesystem domain, while reading or rewriting document "
        "content needs a registered document capability that does not exist "
        "in this phase.",
    ),
    DomainDescriptor(
        CapabilityDomain.EMAIL,
        "Email",
        "Search, read and (only with approval) draft or send mail through the "
        "registered mail connector.",
        DomainPhase.ACTIVE,
        signals=(
            "email",
            "e-mail",
            "emails",
            "gmail",
            "inbox",
            "mailbox",
            "message thread",
            "email thread",
            "send email",
            "draft email",
            "reply to",
        ),
    ),
    DomainDescriptor(
        CapabilityDomain.CALENDAR,
        "Calendar",
        "Read calendars and free time, and create, update or cancel events "
        "only after human review.",
        DomainPhase.ACTIVE,
        signals=(
            "calendar",
            "calendars",
            "meeting",
            "meetings",
            "appointment",
            "appointments",
            "free time",
            "available time",
            "availability",
            "schedule a meeting",
            "reschedule",
        ),
    ),
    DomainDescriptor(
        CapabilityDomain.BROWSER,
        "Browser interaction",
        "Open bounded public pages, interact with declared selectors and "
        "extract page facts through the controlled browser transport.",
        DomainPhase.ACTIVE,
        signals=(
            "browse",
            "browser",
            "website",
            "web page",
            "webpage",
            "fill form",
            "fill the form",
            "fill out",
            "sign in",
            "log in",
            "navigate to",
            "click",
            "on the site",
        ),
    ),
    DomainDescriptor(
        CapabilityDomain.WEB,
        "Public web knowledge",
        "Bounded public search, page reads, deterministic extraction and "
        "source comparison with provenance.",
        DomainPhase.ACTIVE,
        signals=(
            "research",
            "search web",
            "search the web",
            "look up",
            "find information",
            "investigate",
            "compare sources",
            "http://",
            "https://",
            "read this page",
            "open this page",
        ),
    ),
    DomainDescriptor(
        CapabilityDomain.FILESYSTEM,
        "Filesystem workspace",
        "Enumerate, read and (only with explicit approval) write or transform "
        "files strictly inside an approved workspace root.",
        DomainPhase.ACTIVE,
        signals=(
            "file",
            "files",
            "folder",
            "folders",
            "directory",
            "workspace",
            "rename",
            "move",
            "organize",
            "organise",
            "organizing",
            "download",
            "downloads",
            "downloaded",
            "read file",
            "write file",
            "transform file",
        ),
    ),
    DomainDescriptor(
        CapabilityDomain.OS_SHELL,
        "Workspace shell",
        "Run bounded, allowlisted, read-only commands inside the approved "
        "workspace. This is not general shell access and never becomes it.",
        DomainPhase.ACTIVE,
        signals=(
            "run command",
            "shell command",
            "terminal command",
            "py_compile",
            "compile python",
        ),
        notes="Deliberately read-only and allowlisted; no unrestricted shell is "
        "introduced by the digital agent layer.",
    ),
    DomainDescriptor(
        CapabilityDomain.TESTING,
        "Testing and validation",
        "Run the project's automated tests, configured static checks and "
        "deterministic metrics.",
        DomainPhase.ACTIVE,
        signals=(
            "run tests",
            "test suite",
            "pytest",
            "unittest",
            "lint",
            "typecheck",
            "type check",
            "static check",
            "validate",
            "verification suite",
            "metrics",
            "measure",
            "collect stats",
        ),
    ),
    DomainDescriptor(
        CapabilityDomain.GITHUB,
        "GitHub",
        "Inspect repository state and prepare approved source changes. GitHub "
        "is one connector inside the digital agent, not the product.",
        DomainPhase.ACTIVE,
        signals=(
            "github",
            "repository",
            "repo",
            "pull request",
            "pull requests",
            "pr",
            "issue",
            "issues",
            "commit",
            "branch",
            "ci status",
            "merge",
        ),
        notes="Repository-focused engineering remains fully supported through "
        "this domain.",
    ),
)


#: Goal phrases that describe mass destruction. Routing fails closed on these
#: regardless of which domain they mention, because no registered capability
#: performs them and pretending otherwise would be a lie. Phrases are used
#: rather than bare verbs so that narrowly scoped, human-review-gated
#: operations such as "delete event" keep working.
BLOCKED_INTENT_SIGNALS: tuple[str, ...] = (
    "delete all",
    "delete everything",
    "delete the entire",
    "remove all",
    "remove everything",
    "erase everything",
    "wipe",
    "destroy",
    "purge",
    "rm -rf",
    "format the disk",
    "drop database",
    "truncate table",
    "uninstall everything",
)


_DOMAIN_INDEX: Mapping[CapabilityDomain, DomainDescriptor] = {
    item.domain: item for item in DOMAIN_DESCRIPTORS
}


def domain_descriptor(domain: CapabilityDomain | str) -> DomainDescriptor:
    """Return the declared descriptor for a domain, failing closed if unknown."""
    resolved = domain if isinstance(domain, CapabilityDomain) else None
    if resolved is None:
        try:
            resolved = CapabilityDomain(str(domain).strip().lower())
        except ValueError:
            raise ValueError(f"unknown capability domain: {domain!r}") from None
    descriptor = _DOMAIN_INDEX.get(resolved)
    if descriptor is None:
        raise ValueError(f"capability domain is not declared: {resolved.value}")
    return descriptor


def all_domains() -> tuple[DomainDescriptor, ...]:
    return DOMAIN_DESCRIPTORS


def active_domains() -> tuple[DomainDescriptor, ...]:
    return tuple(item for item in DOMAIN_DESCRIPTORS if item.registered)


def reserved_domains() -> tuple[DomainDescriptor, ...]:
    return tuple(item for item in DOMAIN_DESCRIPTORS if item.reserved)


def domain_signals(domain: CapabilityDomain | str) -> tuple[str, ...]:
    return domain_descriptor(domain).signals


def coerce_domain(value: CapabilityDomain | str) -> CapabilityDomain:
    if isinstance(value, CapabilityDomain):
        return value
    try:
        return CapabilityDomain(str(value).strip().lower())
    except ValueError:
        raise ValueError(f"unknown capability domain: {value!r}") from None


def as_domains(values: Iterable[CapabilityDomain | str]) -> tuple[CapabilityDomain, ...]:
    seen: list[CapabilityDomain] = []
    for value in values:
        domain = coerce_domain(value)
        if domain not in seen:
            seen.append(domain)
    return tuple(seen)


__all__ = [
    "BLOCKED_INTENT_SIGNALS",
    "DOMAIN_DESCRIPTORS",
    "DomainDescriptor",
    "DomainPhase",
    "CapabilityDomain",
    "active_domains",
    "all_domains",
    "as_domains",
    "coerce_domain",
    "domain_descriptor",
    "domain_signals",
    "reserved_domains",
]
