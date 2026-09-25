"""Authorization and risk evaluation for digital capabilities.

Two invariants are enforced here:

* **Capabilities cannot self-authorize.** Every decision is produced by the
  process-wide :class:`~autonomous_agent.tool_registry.ToolRegistry` plus the
  consequence-aware approval policy. A capability's own opinion is never
  consulted.
* **Grants are narrowed, never widened.** The set of capabilities a run may use
  is the intersection of what the caller granted and what the *selected*
  capabilities actually need. A caller cannot smuggle ``source_write``,
  ``deploy``, ``billing`` or ``destructive`` into a read-only goal, and the
  permanently-denied set stays denied regardless of what is requested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..capability_policy import DENIED_CAPABILITIES, Capability, CapabilityDecision
from ..consequence_policy import ApprovalMode, ConsequenceAwareApprovalPolicy
from ..prompt_injection_guard import TrustLevel
from ..tool_registry import REGISTRY, ToolRegistry
from .catalog import CapabilityCatalog
from .contract import CapabilityError, DigitalCapability


#: Capabilities that can never be granted to autonomous digital work.
PERMANENTLY_DENIED: frozenset[Capability] = frozenset(DENIED_CAPABILITIES)


@dataclass(frozen=True)
class StepAuthorization:
    """One capability's authorization verdict for one run."""

    capability_id: str
    tool_name: str
    allowed: bool
    reason: str
    capability: str
    risk: str
    read_write: str
    approval_mode: str
    consequence: str
    requires_approval: bool


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    reason: str
    granted: tuple[Capability, ...]
    steps: tuple[StepAuthorization, ...]
    requires_approval: bool
    denied: tuple[str, ...]

    @property
    def blocked(self) -> tuple[str, ...]:
        return tuple(step.capability_id for step in self.steps if not step.allowed)

    def safe_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "granted": tuple(item.value for item in self.granted),
            "requires_approval": self.requires_approval,
            "denied": tuple(self.denied),
            "steps": tuple(
                {
                    "capability_id": step.capability_id,
                    "tool_name": step.tool_name,
                    "allowed": step.allowed,
                    "reason": step.reason,
                    "risk": step.risk,
                    "read_write": step.read_write,
                    "approval_mode": step.approval_mode,
                    "consequence": step.consequence,
                }
                for step in self.steps
            ),
        }


def _coerce(value: Capability | str) -> Capability:
    if isinstance(value, Capability):
        return value
    try:
        return Capability(str(value).strip().lower())
    except ValueError as exc:
        raise CapabilityError(f"unknown capability: {value!r}") from exc


def required_capabilities(capabilities: Iterable[DigitalCapability]) -> tuple[Capability, ...]:
    """The exact capability set a group of capabilities needs -- nothing more."""
    seen: list[Capability] = []
    for capability in capabilities:
        value = _coerce(capability.spec.capability)
        if value not in seen:
            seen.append(value)
    return tuple(sorted(seen, key=lambda item: item.value))


def autonomous_capabilities(capabilities: Iterable[DigitalCapability]) -> tuple[Capability, ...]:
    """Capabilities that may be used without human approval.

    Restricted to tools the registry marks ``safe_autonomous`` (which the
    registry itself forces to be read-only), so an unattended run can never
    reach a side effect.
    """
    return required_capabilities(
        capability for capability in capabilities if capability.spec.safe_autonomous
    )


def narrow_grants(
    capabilities: Iterable[DigitalCapability],
    requested: Iterable[Capability | str] = (),
) -> tuple[Capability, ...]:
    """Intersect caller grants with what the selected capabilities need.

    Permanently denied capabilities are dropped even when explicitly requested.
    """
    needed = set(required_capabilities(capabilities))
    granted: list[Capability] = []
    for item in requested:
        value = _coerce(item)
        if value in PERMANENTLY_DENIED:
            continue
        if value in needed and value not in granted:
            granted.append(value)
    return tuple(sorted(granted, key=lambda entry: entry.value))


def assert_no_escalation(
    capabilities: Sequence[DigitalCapability],
    granted: Iterable[Capability | str],
) -> None:
    """Fail closed if a grant is not justified by a selected capability."""
    needed = set(required_capabilities(capabilities))
    for item in granted:
        value = _coerce(item)
        if value in PERMANENTLY_DENIED:
            raise CapabilityError(
                f"capability escalation blocked: {value.value} is permanently denied"
            )
        if value not in needed:
            raise CapabilityError(
                f"capability escalation blocked: {value.value} is not required by any "
                f"selected capability"
            )


class CapabilityAuthorizationBroker:
    """Evaluate authorization for planned capability steps.

    The broker owns no permissions. It asks the tool registry and the
    consequence-aware policy, then reports a verdict.
    """

    def __init__(
        self,
        catalog: CapabilityCatalog,
        *,
        tool_registry: ToolRegistry = REGISTRY,
        approval_policy: ConsequenceAwareApprovalPolicy | None = None,
    ) -> None:
        self._catalog = catalog
        self._tool_registry = tool_registry
        self._approval_policy = approval_policy or ConsequenceAwareApprovalPolicy()

    def evaluate_step(
        self,
        capability_id: str,
        granted: Iterable[Capability | str] = (),
        *,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
        origin_trust: TrustLevel = TrustLevel.USER,
    ) -> StepAuthorization:
        capability = self._catalog.get(capability_id)
        if capability is None:
            # Unknown capabilities fail closed: never inferred, never defaulted.
            return StepAuthorization(
                capability_id,
                "",
                False,
                "capability is not registered in the catalog",
                "",
                "critical",
                "unknown",
                ApprovalMode.DENY.value,
                "critical",
                True,
            )
        spec = capability.spec
        decision: CapabilityDecision = self._tool_registry.authorize(
            spec.name,
            granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
        )
        approval = self._approval_policy.evaluate(
            spec, origin_trust=origin_trust, explicitly_approved=explicitly_approved
        )
        if approval.mode is ApprovalMode.DENY:
            allowed, reason = False, "consequence-aware policy denies this action"
        elif approval.mode is ApprovalMode.REQUIRE_APPROVAL and not explicitly_approved:
            allowed, reason = False, "action has meaningful side effects and requires approval"
        elif origin_trust in {TrustLevel.EXTERNAL, TrustLevel.TOOL_RESULT, TrustLevel.MEMORY} and (
            spec.read_write_mode.value != "read_only" and not explicitly_approved
        ):
            allowed, reason = False, "untrusted content cannot authorize a side effect"
        else:
            allowed, reason = decision.allowed, decision.reason
        return StepAuthorization(
            capability_id,
            spec.name,
            allowed,
            reason,
            spec.capability,
            spec.risk_level.value,
            spec.read_write_mode.value,
            approval.mode.value,
            approval.consequence.value,
            approval.mode is not ApprovalMode.AUTONOMOUS,
        )

    def evaluate(
        self,
        capability_ids: Iterable[str],
        granted: Iterable[Capability | str] = (),
        *,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
        origin_trust: TrustLevel = TrustLevel.USER,
    ) -> AuthorizationResult:
        capabilities: list[DigitalCapability] = []
        steps: list[StepAuthorization] = []
        denied: list[str] = []
        for capability_id in capability_ids:
            step = self.evaluate_step(
                capability_id,
                granted,
                explicitly_approved=explicitly_approved,
                sandbox_available=sandbox_available,
                audit_available=audit_available,
                origin_trust=origin_trust,
            )
            steps.append(step)
            if not step.allowed:
                denied.append(capability_id)
            resolved = self._catalog.get(capability_id)
            if resolved is not None:
                capabilities.append(resolved)
        effective = narrow_grants(capabilities, granted)
        requires_approval = any(step.requires_approval for step in steps)
        allowed = not denied
        if allowed:
            reason = "every selected capability is authorized by the tool registry"
        else:
            reason = "; ".join(
                f"{step.capability_id}: {step.reason}" for step in steps if not step.allowed
            )
        return AuthorizationResult(
            allowed, reason, effective, tuple(steps), requires_approval, tuple(denied)
        )


__all__ = [
    "PERMANENTLY_DENIED",
    "AuthorizationResult",
    "CapabilityAuthorizationBroker",
    "StepAuthorization",
    "assert_no_escalation",
    "autonomous_capabilities",
    "narrow_grants",
    "required_capabilities",
]
