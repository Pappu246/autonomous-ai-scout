from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .capability_policy import CapabilityDecision
from .connector_registry import ConnectorRegistry
from .tool_registry import REGISTRY, ReadWriteMode, ToolRegistry, ToolSpec
from .prompt_injection_guard import TrustLevel
from .consequence_policy import ApprovalMode, ConsequenceAwareApprovalPolicy


@dataclass(frozen=True)
class ToolCandidate:
    name: str
    description: str
    category: str
    risk_level: str
    capability: str
    approval_required: str
    connector_ids: tuple[str, ...]


@dataclass(frozen=True)
class ToolInvocation:
    tool_name: str
    arguments: Mapping[str, Any]
    requested_at: str


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    success: bool
    output: Any
    error: str = ""


Invoker = Callable[[ToolInvocation], Any]


class UniversalDigitalToolLayer:
    """Common discovery, validation, authorization and invocation boundary."""

    def __init__(
        self,
        *,
        registry: ToolRegistry = REGISTRY,
        connectors: Iterable[ConnectorRegistry] = (),
        approval_policy: ConsequenceAwareApprovalPolicy | None = None,
    ) -> None:
        self.registry = registry
        self.connectors = tuple(connectors)
        self.approval_policy = approval_policy or ConsequenceAwareApprovalPolicy()

    def discover(
        self,
        query: str = "",
        *,
        category: str | None = None,
        capability: str | None = None,
    ) -> tuple[ToolCandidate, ...]:
        text = query.strip().lower()
        results: list[ToolCandidate] = []
        for spec in self.registry.list():
            if category and spec.category != category:
                continue
            if capability and spec.capability != capability:
                continue
            haystack = f"{spec.name} {spec.description} {spec.category}".lower()
            if text and text not in haystack:
                continue
            connector_ids = tuple(
                sorted(
                    connector.connector_id
                    for registry in self.connectors
                    for connector in registry.list()
                    if spec.name in connector.tool_names
                )
            )
            results.append(
                ToolCandidate(
                    spec.name,
                    spec.description,
                    spec.category,
                    spec.risk_level.value,
                    spec.capability,
                    spec.approval_requirement.value,
                    connector_ids,
                )
            )
        return tuple(sorted(results, key=lambda item: item.name))

    def resolve(self, tool_name: str) -> ToolSpec | None:
        return self.registry.get(tool_name)

    @staticmethod
    def _validate_arguments(spec: ToolSpec, arguments: Mapping[str, Any]) -> str | None:
        if not isinstance(arguments, Mapping):
            return "tool arguments must be an object"
        schema = spec.input_schema
        if schema.get("type") != "object":
            return None
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}))
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                return f"unknown tool arguments: {', '.join(unknown)}"
        return None

    def authorize(
        self,
        tool_name: str,
        granted=(),
        *,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
    ) -> CapabilityDecision:
        return self.registry.authorize(
            tool_name,
            granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
        )

    def invoke(
        self,
        invocation: ToolInvocation,
        *,
        granted=(),
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
        invoker: Invoker | None = None,
        origin_trust: TrustLevel = TrustLevel.USER,
    ) -> ToolResult:
        spec = self.resolve(invocation.tool_name)
        if spec is None:
            return ToolResult(invocation.tool_name, False, None, "tool is not registered")
        invalid = self._validate_arguments(spec, invocation.arguments)
        if invalid:
            return ToolResult(spec.name, False, None, invalid)
        if origin_trust in {TrustLevel.EXTERNAL, TrustLevel.TOOL_RESULT, TrustLevel.MEMORY} and spec.read_write_mode is not ReadWriteMode.READ_ONLY and not explicitly_approved:
            return ToolResult(spec.name, False, None, "untrusted content cannot authorize a write action")
        consequence = self.approval_policy.evaluate(
            spec,
            origin_trust=origin_trust,
            explicitly_approved=explicitly_approved,
        )
        if consequence.mode is ApprovalMode.REQUIRE_APPROVAL and not explicitly_approved:
            return ToolResult(spec.name, False, None, "consequence-aware policy requires explicit approval")
        if consequence.mode is ApprovalMode.DENY:
            return ToolResult(spec.name, False, None, "consequence-aware policy denies this action")
        decision = self.authorize(
            spec.name,
            granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
        )
        if not decision.allowed:
            return ToolResult(spec.name, False, None, decision.reason)
        if invoker is None:
            return ToolResult(spec.name, False, None, "no execution adapter is registered")
        try:
            return ToolResult(spec.name, True, invoker(invocation))
        except Exception as exc:
            return ToolResult(spec.name, False, None, f"tool invocation failed: {type(exc).__name__}")


__all__ = ["Invoker", "ToolCandidate", "ToolInvocation", "ToolResult", "UniversalDigitalToolLayer"]
