"""The canonical digital capability contract.

Every digital capability -- filesystem, browser, email, GitHub, a future
computer-control adapter -- implements the same lifecycle:

    discover() -> validate_input() -> authorize() -> execute()
             -> observe() -> verify() -> bounded_retry() -> audit()

Three properties are structural rather than conventional:

1. ``authorize()`` is *not* an authority. A capability cannot grant itself
   anything: the implementation must delegate to the process-wide
   :class:`~autonomous_agent.tool_registry.ToolRegistry`, and the runtime
   re-checks authorization through that registry immediately before executing.
   A capability that lies about authorization is still blocked.

2. ``verify()`` cannot be satisfied by a successful call alone. Verification
   requires observable evidence, so a no-op that returns success is never
   reported as ``VERIFIED``.

3. ``bounded_retry()`` is bounded by a declared, validated retry policy and
   never retries a step that already produced verified evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from ..capability_policy import Capability, CapabilityDecision
from .domains import CapabilityDomain, DomainPhase, coerce_domain


MAX_RETRY_ATTEMPTS = 3
MAX_RETRY_BACKOFF_SECONDS = 300

_SECRET_MATERIAL = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|password|passwd|"
    r"secret|private[_-]?key|client[_-]?secret)\s*[:=]\s*\S+"
)


class CapabilityAvailability(str, Enum):
    """Whether a capability can be planned and executed right now."""

    AVAILABLE = "available"
    UNREGISTERED = "unregistered"
    DISABLED = "disabled"


class CapabilityError(ValueError):
    """Raised for contract violations that must fail closed."""


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry budget for one capability.

    Attempts are capped at :data:`MAX_RETRY_ATTEMPTS` and only idempotent or
    naturally-idempotent operations may declare more than one attempt.
    """

    max_attempts: int = 1
    backoff_seconds: int = 0

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_attempts) <= MAX_RETRY_ATTEMPTS:
            raise CapabilityError("retry attempts are outside the bounded safe limit")
        if not 0 <= int(self.backoff_seconds) <= MAX_RETRY_BACKOFF_SECONDS:
            raise CapabilityError("retry backoff is outside the bounded safe limit")
        if self.backoff_seconds and self.max_attempts < 2:
            raise CapabilityError("backoff requires more than one attempt")


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Non-secret, self-describing metadata for one registered capability.

    This is what the planner, the documentation model and the audit trail see.
    It never carries credential material; the constructor refuses it.
    """

    capability_id: str
    domain: CapabilityDomain
    tool_name: str
    description: str
    capability: str
    risk: str
    read_write: str
    network: str
    approval: str
    sandbox: str
    audit: str
    safe_autonomous: bool
    availability: CapabilityAvailability = CapabilityAvailability.AVAILABLE
    signals: tuple[str, ...] = ()
    stage: int = 50
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    credential_handling: str = "none"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.capability_id or ":" not in self.capability_id:
            raise CapabilityError("capability id must be namespaced as <domain>:<operation>")
        if not self.capability_id.startswith(f"{coerce_domain(self.domain).value}:"):
            raise CapabilityError("capability id namespace must match its domain")
        if not self.tool_name:
            raise CapabilityError("capability must bind to a registered tool")
        if self.stage < 0 or self.stage > 1000:
            raise CapabilityError("capability stage must be within 0..1000")
        if any(not isinstance(signal, str) or not signal.strip() for signal in self.signals):
            raise CapabilityError("capability signals must be non-empty strings")
        for value in (self.description, self.notes, self.credential_handling):
            if _SECRET_MATERIAL.search(str(value)):
                raise CapabilityError("capability descriptor must not contain credential material")

    @property
    def operation(self) -> str:
        return self.capability_id.split(":", 1)[1]

    @property
    def writes(self) -> bool:
        return self.read_write != "read_only"

    def safe_dict(self) -> dict[str, object]:
        """Redacted, JSON-safe projection suitable for logs and model context."""
        return {
            "capability_id": self.capability_id,
            "domain": self.domain.value,
            "tool_name": self.tool_name,
            "description": self.description,
            "risk": self.risk,
            "read_write": self.read_write,
            "network": self.network,
            "approval": self.approval,
            "safe_autonomous": self.safe_autonomous,
            "availability": self.availability.value,
            "credential_handling": self.credential_handling,
            "retry": {
                "max_attempts": self.retry_policy.max_attempts,
                "backoff_seconds": self.retry_policy.backoff_seconds,
            },
            "stage": self.stage,
        }


@dataclass(frozen=True)
class InputValidation:
    ok: bool
    reason: str
    normalized: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityRequest:
    """One bounded request for one capability."""

    capability_id: str
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    credential_reference: str | None = None
    approved: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.arguments, Mapping):
            raise CapabilityError("capability arguments must be an object")
        if self.credential_reference is not None:
            reference = str(self.credential_reference)
            if not reference.startswith("credref:") or len(reference) > 256:
                raise CapabilityError("credential input must be a bounded credref identifier")
            if _SECRET_MATERIAL.search(reference):
                raise CapabilityError("raw credential material is not accepted")

    @property
    def digest(self) -> str:
        payload = {
            "capability_id": self.capability_id,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "credential_reference": self.credential_reference,
            "approved": bool(self.approved),
        }
        import hashlib

        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class CapabilityExecution:
    """The raw outcome of running one capability request."""

    capability_id: str
    success: bool
    evidence: Mapping[str, Any] = field(default_factory=dict)
    output: str = ""
    error: str = ""
    exit_status: int | None = None
    output_truncated: bool = False
    boundary: str = ""
    attempts: int = 1

    @property
    def has_evidence(self) -> bool:
        """True only when the boundary returned something checkable."""
        return bool(self.evidence) and any(
            value not in (None, "", (), [], {}) for value in self.evidence.values()
        )


@dataclass(frozen=True)
class CapabilityObservation:
    """What was actually observable after execution."""

    capability_id: str
    observed: bool
    evidence: Mapping[str, Any] = field(default_factory=dict)
    detail: str = ""

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence) and any(
            value not in (None, "", (), [], {}) for value in self.evidence.values()
        )


@dataclass(frozen=True)
class CapabilityVerification:
    verified: bool
    reason: str
    evidence_present: bool
    detail: str = ""


@dataclass(frozen=True)
class CapabilityAuditRecord:
    """One append-only audit entry for a capability step."""

    execution_id: str
    capability_id: str
    tool_name: str
    event: str
    state: str
    detail: str = ""
    attempt: int = 1
    verified: bool = False
    step_id: str = ""

    def as_record(self, timestamp: str | None = None) -> dict[str, str]:
        return {
            "execution_id": self.execution_id,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "event": self.event,
            "capability_id": self.capability_id,
            "tool_name": self.tool_name,
            "state": self.state,
            "attempt": str(int(self.attempt)),
            "result": "success" if self.verified else "failure",
            "verification": "verified" if self.verified else "unverified",
            "detail": self.detail,
            "step_id": self.step_id,
        }


@dataclass(frozen=True)
class CapabilityOutcome:
    """The end-to-end result of one capability step."""

    capability_id: str
    tool_name: str
    state: str
    reason: str
    attempts: int
    execution: CapabilityExecution | None
    observation: CapabilityObservation | None
    verification: CapabilityVerification | None

    @property
    def verified(self) -> bool:
        return self.state == "verified"


@runtime_checkable
class DigitalCapability(Protocol):
    """The interface every digital capability adapter must satisfy."""

    @property
    def descriptor(self) -> CapabilityDescriptor: ...

    def discover(self) -> CapabilityDescriptor: ...

    def validate_input(self, arguments: Mapping[str, Any]) -> InputValidation: ...

    def authorize(
        self,
        granted: Iterable[Capability | str] = (),
        *,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
    ) -> CapabilityDecision: ...

    def execute(self, request: CapabilityRequest) -> CapabilityExecution: ...

    def observe(
        self, request: CapabilityRequest, execution: CapabilityExecution
    ) -> CapabilityObservation: ...

    def verify(
        self,
        request: CapabilityRequest,
        execution: CapabilityExecution,
        observation: CapabilityObservation,
    ) -> CapabilityVerification: ...

    def bounded_retry(self, request: CapabilityRequest) -> CapabilityOutcome: ...

    def bind_audit_sink(self, sink: Any) -> None: ...

    def audit(self, record: CapabilityAuditRecord) -> None: ...


__all__ = [
    "MAX_RETRY_ATTEMPTS",
    "MAX_RETRY_BACKOFF_SECONDS",
    "CapabilityAuditRecord",
    "CapabilityAvailability",
    "CapabilityDescriptor",
    "CapabilityError",
    "CapabilityExecution",
    "CapabilityObservation",
    "CapabilityOutcome",
    "CapabilityRequest",
    "CapabilityVerification",
    "DigitalCapability",
    "DomainPhase",
    "InputValidation",
    "RetryPolicy",
]
