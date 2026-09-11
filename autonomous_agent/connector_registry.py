from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .capability_policy import Capability, CapabilityDecision
from .tool_registry import ToolRegistry, REGISTRY, RiskLevel, ReadWriteMode, NetworkRequirement, ApprovalRequirement, SandboxRequirement, AuditRequirement


class ConnectorAuth(str, Enum):
    NONE = "none"
    USER_AUTH = "user_auth"
    SERVICE_AUTH = "service_auth"
    ELEVATED_AUTH = "elevated_auth"


@dataclass(frozen=True)
class ConnectorSpec:
    """Declarative connector metadata; it has no independent permission model."""

    identity: str
    capabilities: tuple[str, ...]
    scopes: tuple[str, ...]
    authentication_method: ConnectorAuth
    network_required: bool
    risk: str
    read_write_mode: str
    approval_required: bool
    sandbox_required: bool
    audit_required: bool
    registered_tools: tuple[str, ...]
    enabled: bool = True


BUILTIN_CONNECTORS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec("github", ("repository.inspect",), ("repository:read",), ConnectorAuth.USER_AUTH, True, "low", "read_only", False, True, True, ("github.inspect",)),
    ConnectorSpec("web_research", ("web.fetch",), ("explicit-resource",), ConnectorAuth.USER_AUTH, True, "high", "read_only", True, True, True, ("network.fetch",)),
    ConnectorSpec("files", ("workspace.read",), ("approved-workspace",), ConnectorAuth.NONE, False, "low", "read_only", False, True, True, ("filesystem.read",)),
    ConnectorSpec("ai_providers", ("model.benchmark",), ("configured-free-provider",), ConnectorAuth.SERVICE_AUTH, True, "medium", "read_only", False, True, True, ("model.benchmark",)),
    ConnectorSpec("project_repository", ("repository.inspect", "repository.change"), ("repository:read", "reviewed-change"), ConnectorAuth.USER_AUTH, True, "high", "controlled_write", True, True, True, ("github.inspect", "github.change")),
    ConnectorSpec("email", ("email.read", "email.send"), ("mailbox:read", "mailbox:send"), ConnectorAuth.USER_AUTH, True, "high", "controlled_write", True, True, True, (), False),
    ConnectorSpec("calendar_api", ("calendar.read", "calendar.write"), ("calendar:read", "calendar:write"), ConnectorAuth.USER_AUTH, True, "high", "controlled_write", True, True, True, (), False),
    ConnectorSpec("browser", ("browser.automation",), ("explicit-site",), ConnectorAuth.USER_AUTH, True, "critical", "controlled_write", True, True, True, (), False),
)


class ConnectorRegistryError(ValueError):
    pass


class ConnectorRegistry:
    """Connector catalog that delegates all authority to the existing Tool Registry."""

    def __init__(self, specs: Iterable[ConnectorSpec] = BUILTIN_CONNECTORS):
        self._connectors: dict[str, ConnectorSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ConnectorSpec) -> ConnectorSpec:
        if not spec.identity or spec.identity != spec.identity.strip().lower() or " " in spec.identity:
            raise ConnectorRegistryError("connector identity must be normalized and contain no spaces")
        if spec.identity in self._connectors:
            raise ConnectorRegistryError(f"connector already registered: {spec.identity}")
        if not spec.capabilities or not spec.scopes:
            raise ConnectorRegistryError("connector capabilities and scopes are required")
        if spec.approval_required and spec.risk not in {"high", "critical"}:
            raise ConnectorRegistryError("approval-required connector must declare high or critical risk")
        for tool_name in spec.registered_tools:
            tool = REGISTRY.get(tool_name)
            if tool is None:
                raise ConnectorRegistryError(f"connector references unknown registered tool: {tool_name}")
            if spec.network_required is False and tool.network_requirement is NetworkRequirement.REQUIRED:
                raise ConnectorRegistryError("connector metadata cannot disable a required network")
            if spec.sandbox_required and tool.sandbox_requirement is not SandboxRequirement.REQUIRED:
                raise ConnectorRegistryError("connector sandbox metadata is inconsistent with its tool contract")
            if spec.audit_required and tool.audit_requirement is not AuditRequirement.REQUIRED:
                raise ConnectorRegistryError("connector audit metadata is inconsistent with its tool contract")
            if spec.approval_required is False and tool.approval_requirement is not ApprovalRequirement.NONE:
                raise ConnectorRegistryError("connector cannot weaken a tool approval requirement")
            if spec.risk == "low" and tool.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                raise ConnectorRegistryError("connector cannot lower tool risk")
            if spec.read_write_mode == "read_only" and tool.read_write_mode is not ReadWriteMode.READ_ONLY:
                raise ConnectorRegistryError("connector cannot make a writable tool read-only")
        self._connectors[spec.identity] = spec
        return spec

    def get(self, identity: str) -> ConnectorSpec | None:
        if not isinstance(identity, str):
            return None
        return self._connectors.get(identity.strip().lower())

    def list(self) -> tuple[ConnectorSpec, ...]:
        return tuple(self._connectors.values())

    def authorize(
        self,
        identity: str,
        granted: Iterable[Capability | str] = (),
        *,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
        registry: ToolRegistry = REGISTRY,
    ) -> CapabilityDecision:
        spec = self.get(identity)
        if spec is None:
            return CapabilityDecision(False, "connector is not registered", "")
        if not spec.enabled:
            return CapabilityDecision(False, "connector is disabled until a registered tool boundary exists", "")
        if not spec.registered_tools:
            return CapabilityDecision(False, "connector has no registered executable tool", "")
        for tool_name in spec.registered_tools:
            decision = registry.authorize(
                tool_name,
                granted,
                explicitly_approved=explicitly_approved,
                sandbox_available=sandbox_available,
                audit_available=audit_available,
            )
            if not decision.allowed:
                return decision
        return CapabilityDecision(True, "all connector tools are permitted by the existing Tool Registry and capability policy", spec.registered_tools[0])


CONNECTORS = ConnectorRegistry()


def list_connectors() -> tuple[ConnectorSpec, ...]:
    return CONNECTORS.list()


def get_connector(identity: str) -> ConnectorSpec | None:
    return CONNECTORS.get(identity)


def authorize_connector(
    identity: str,
    granted: Iterable[Capability | str] = (),
    *,
    explicitly_approved: bool = False,
    sandbox_available: bool = True,
    audit_available: bool = True,
) -> CapabilityDecision:
    return CONNECTORS.authorize(identity, granted, explicitly_approved=explicitly_approved, sandbox_available=sandbox_available, audit_available=audit_available)
