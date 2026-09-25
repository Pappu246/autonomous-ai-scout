"""The capability catalog: discovery, goal routing and documentation.

The catalog is the only thing the planner consults to decide *what* a goal
needs. All vocabulary is data attached to registered capabilities and declared
domains, which is what makes the planner domain-agnostic: adding a browser,
computer or application adapter means registering a capability with its own
signals -- the planner is never edited.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from .builtins import DEFAULT_CAPABILITIES
from .contract import (
    CapabilityAvailability,
    CapabilityDescriptor,
    CapabilityError,
    DigitalCapability,
)
from .domains import (
    BLOCKED_INTENT_SIGNALS,
    DOMAIN_DESCRIPTORS,
    CapabilityDomain,
    DomainDescriptor,
    coerce_domain,
    domain_descriptor,
)


def _normalize(text: str) -> str:
    return " ".join(str(text).strip().split()).lower()


_WORD_SIGNAL = re.compile(r"^[\w'\- ]+$")


def signal_matches(signal: str, text: str) -> bool:
    """Match a routing signal against a normalized goal.

    Word-shaped signals are matched on word boundaries so that short signals
    cannot produce false positives (``repo`` must not match ``report``,
    ``move`` must not match ``remove``, ``file`` must not match ``profile``).
    Signals containing punctuation -- URLs, for example -- are matched as
    substrings because word boundaries do not apply to them.
    """
    needle = signal.strip().lower()
    if not needle:
        return False
    haystack = text.lower()
    if not _WORD_SIGNAL.match(needle):
        return needle in haystack
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None


@dataclass(frozen=True)
class DomainMatch:
    domain: CapabilityDomain
    score: int
    matched_signals: tuple[str, ...]
    registered: bool


@dataclass(frozen=True)
class RoutingResult:
    """Which domains a goal needs and which capabilities were selected."""

    goal: str
    matches: tuple[DomainMatch, ...]
    selected: tuple[str, ...]
    reserved_required: tuple[CapabilityDomain, ...]
    reason: str

    @property
    def matched_domains(self) -> tuple[CapabilityDomain, ...]:
        return tuple(match.domain for match in self.matches)

    @property
    def routed(self) -> bool:
        return bool(self.selected) and not self.reserved_required

    def safe_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal,
            "matched_domains": tuple(match.domain.value for match in self.matches),
            "selected": tuple(self.selected),
            "reserved_required": tuple(item.value for item in self.reserved_required),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CapabilityDocumentation:
    """Machine-readable documentation for one capability."""

    capability_id: str
    domain: str
    tool_name: str
    description: str
    risk: str
    read_write: str
    approval: str
    safe_autonomous: bool
    availability: str
    credential_handling: str
    signals: tuple[str, ...]
    retry: Mapping[str, int]
    stage: int
    notes: str = ""


@dataclass(frozen=True)
class DomainDocumentation:
    domain: str
    title: str
    description: str
    phase: str
    capability_ids: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class CapabilityCatalogDocumentation:
    version: int
    domains: tuple[DomainDocumentation, ...]
    capabilities: tuple[CapabilityDocumentation, ...]
    digest: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "digest": self.digest,
                "domains": [
                    {
                        "domain": item.domain,
                        "title": item.title,
                        "description": item.description,
                        "phase": item.phase,
                        "capability_ids": list(item.capability_ids),
                        "notes": item.notes,
                    }
                    for item in self.domains
                ],
                "capabilities": [
                    {
                        "capability_id": item.capability_id,
                        "domain": item.domain,
                        "tool_name": item.tool_name,
                        "description": item.description,
                        "risk": item.risk,
                        "read_write": item.read_write,
                        "approval": item.approval,
                        "safe_autonomous": item.safe_autonomous,
                        "availability": item.availability,
                        "credential_handling": item.credential_handling,
                        "signals": list(item.signals),
                        "retry": dict(item.retry),
                        "stage": item.stage,
                        "notes": item.notes,
                    }
                    for item in self.capabilities
                ],
            },
            indent=2,
            sort_keys=True,
        )


class CapabilityCatalog:
    """Index of registered digital capabilities plus routing over them."""

    def __init__(
        self,
        capabilities: Iterable[DigitalCapability] = (),
        *,
        domains: Iterable[DomainDescriptor] = DOMAIN_DESCRIPTORS,
    ) -> None:
        self._domains = {item.domain: item for item in domains}
        self._items: dict[str, DigitalCapability] = {}
        self._by_tool: dict[str, str] = {}
        for capability in capabilities:
            self.register(capability)

    # -- registration -----------------------------------------------------
    def register(self, capability: DigitalCapability) -> CapabilityDescriptor:
        descriptor = capability.discover()
        if descriptor.domain not in self._domains:
            raise CapabilityError(
                f"capability domain is not declared: {descriptor.domain.value}"
            )
        if descriptor.capability_id in self._items:
            raise CapabilityError(f"duplicate capability id: {descriptor.capability_id}")
        existing = self._by_tool.get(descriptor.tool_name)
        if existing is not None:
            raise CapabilityError(
                f"tool is already bound to capability {existing}: {descriptor.tool_name}"
            )
        self._items[descriptor.capability_id] = capability
        self._by_tool[descriptor.tool_name] = descriptor.capability_id
        return descriptor

    # -- discovery --------------------------------------------------------
    def get(self, capability_id: str) -> DigitalCapability | None:
        return self._items.get(str(capability_id).strip().lower())

    def by_tool(self, tool_name: str) -> DigitalCapability | None:
        capability_id = self._by_tool.get(str(tool_name).strip().lower())
        return self._items.get(capability_id) if capability_id else None

    def capabilities(
        self, *, domain: CapabilityDomain | str | None = None
    ) -> tuple[DigitalCapability, ...]:
        resolved = coerce_domain(domain) if domain is not None else None
        return tuple(
            sorted(
                (
                    item
                    for item in self._items.values()
                    if resolved is None or item.descriptor.domain is resolved
                ),
                key=lambda item: (item.descriptor.domain.value, item.descriptor.stage, item.descriptor.capability_id),
            )
        )

    def discover(
        self,
        query: str = "",
        *,
        domain: CapabilityDomain | str | None = None,
        availability: CapabilityAvailability | None = None,
    ) -> tuple[CapabilityDescriptor, ...]:
        """Return descriptors matching a free-text query and/or filters."""
        text = _normalize(query)
        results: list[CapabilityDescriptor] = []
        for capability in self.capabilities(domain=domain):
            descriptor = capability.discover()
            if availability is not None and descriptor.availability is not availability:
                continue
            haystack = " ".join(
                (
                    descriptor.capability_id,
                    descriptor.tool_name,
                    descriptor.description,
                    descriptor.domain.value,
                    *descriptor.signals,
                )
            ).lower()
            if text and text not in haystack:
                continue
            results.append(descriptor)
        return tuple(results)

    def domains(self) -> tuple[DomainDocumentation, ...]:
        return tuple(
            DomainDocumentation(
                domain=descriptor.domain.value,
                title=descriptor.title,
                description=descriptor.description,
                phase=descriptor.phase.value,
                capability_ids=tuple(
                    item.descriptor.capability_id
                    for item in self.capabilities(domain=descriptor.domain)
                ),
                notes=descriptor.notes,
            )
            for descriptor in DOMAIN_DESCRIPTORS
        )

    def domain_status(self) -> tuple[dict[str, object], ...]:
        """Honest availability of every declared domain, reserved included."""
        return tuple(
            {
                "domain": descriptor.domain.value,
                "title": descriptor.title,
                "phase": descriptor.phase.value,
                "registered_capabilities": len(self.capabilities(domain=descriptor.domain)),
                "usable": self._domain_is_usable(descriptor.domain),
            }
            for descriptor in DOMAIN_DESCRIPTORS
        )

    def documentation(self, version: int = 1) -> CapabilityCatalogDocumentation:
        """Build the redacted documentation model for adapters and humans."""
        capabilities = tuple(
            CapabilityDocumentation(
                capability_id=item.descriptor.capability_id,
                domain=item.descriptor.domain.value,
                tool_name=item.descriptor.tool_name,
                description=item.descriptor.description,
                risk=item.descriptor.risk,
                read_write=item.descriptor.read_write,
                approval=item.descriptor.approval,
                safe_autonomous=item.descriptor.safe_autonomous,
                availability=item.descriptor.availability.value,
                credential_handling=item.descriptor.credential_handling,
                signals=tuple(item.descriptor.signals),
                retry={
                    "max_attempts": item.descriptor.retry_policy.max_attempts,
                    "backoff_seconds": item.descriptor.retry_policy.backoff_seconds,
                },
                stage=item.descriptor.stage,
                notes=item.descriptor.notes,
            )
            for item in self.capabilities()
        )
        domains = self.domains()
        payload = {
            "capabilities": [item.capability_id for item in capabilities],
            "domains": [item.domain for item in domains],
            "version": version,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return CapabilityCatalogDocumentation(version, domains, capabilities, digest)

    # -- routing ----------------------------------------------------------
    def route(self, goal: str) -> RoutingResult:
        """Map a natural-language goal onto registered capabilities.

        Selection rules, in order:

        1. Match declared *domain* signals against the goal.
        2. Within each matched active domain, prefer capabilities whose own
           signals matched; otherwise fall back to that domain's declared
           read-only defaults.
        3. A matched *reserved* domain (computer control, application adapter,
           document processing) is reported as required but unavailable, so the
           caller fails closed instead of pretending to have done the work.
        4. Write/side-effect capabilities are only ever selected by an explicit
           capability-level signal match, never by a domain fallback.
        """
        text = _normalize(goal)
        if not text:
            return RoutingResult("", (), (), (), "goal is empty; nothing can be routed")

        destructive = tuple(
            signal for signal in BLOCKED_INTENT_SIGNALS if signal_matches(signal, text)
        )
        if destructive:
            return RoutingResult(
                text,
                (),
                (),
                (),
                "goal requests a destructive action that no registered capability "
                f"performs ({', '.join(destructive)}); routing fails closed",
            )

        # Matching uses declared *domain* vocabulary only. Keeping capability
        # vocabulary out of domain matching is deliberate: it stops one
        # adapter's wording from pulling an unrelated domain into a plan.
        # Adding a new domain is one declarative DomainDescriptor entry; the
        # planner itself never changes.
        matches: list[DomainMatch] = []
        for descriptor in DOMAIN_DESCRIPTORS:
            hit = tuple(signal for signal in descriptor.signals if signal_matches(signal, text))
            if not hit:
                continue
            matches.append(
                DomainMatch(
                    descriptor.domain,
                    sum(len(signal) for signal in hit),
                    hit,
                    self._domain_is_usable(descriptor.domain),
                )
            )
        matches.sort(key=lambda item: (-item.score, item.domain.value))
        if not matches:
            return RoutingResult(
                _normalize(goal),
                (),
                (),
                (),
                "no declared capability domain matches the goal; routing fails closed",
            )

        reserved = tuple(match.domain for match in matches if not match.registered)
        selected: list[str] = []
        for match in matches:
            if not match.registered:
                continue
            for capability in self.capabilities(domain=match.domain):
                descriptor = capability.discover()
                if descriptor.availability is not CapabilityAvailability.AVAILABLE:
                    continue
                if any(signal_matches(signal, text) for signal in descriptor.signals):
                    if descriptor.capability_id not in selected:
                        selected.append(descriptor.capability_id)
        for match in matches:
            if not match.registered:
                continue
            if any(
                capability_id.startswith(f"{match.domain.value}:")
                for capability_id in selected
            ):
                continue
            for capability_id in DEFAULT_CAPABILITIES.get(match.domain, ()):  # read-only fallback
                if self.get(capability_id) is not None and capability_id not in selected:
                    selected.append(capability_id)

        ordered = tuple(
            sorted(
                selected,
                key=lambda capability_id: (
                    self._items[capability_id].descriptor.stage,
                    capability_id,
                ),
            )
        )
        if reserved:
            names = ", ".join(
                domain_descriptor(item).title for item in reserved
            )
            reason = (
                f"goal requires capability domains that are not registered in this "
                f"phase: {names}"
            )
        elif ordered:
            reason = "routed to the narrowest registered capability set for the goal"
        else:
            reason = "matched domains expose no usable capability"
        return RoutingResult(_normalize(goal), tuple(matches), ordered, reserved, reason)

    def _domain_is_usable(self, domain: CapabilityDomain) -> bool:
        return any(
            item.descriptor.availability is CapabilityAvailability.AVAILABLE
            for item in self.capabilities(domain=domain)
        )


__all__ = [
    "CapabilityCatalog",
    "CapabilityCatalogDocumentation",
    "CapabilityDocumentation",
    "DomainDocumentation",
    "DomainMatch",
    "RoutingResult",
    "signal_matches",
]
