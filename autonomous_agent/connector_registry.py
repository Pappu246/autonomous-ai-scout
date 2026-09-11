from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .capability_policy import Capability, CapabilityDecision
from .tool_registry import (
    ApprovalRequirement,
    AuditRequirement,
    NetworkRequirement,
    ReadWriteMode,
    RiskLevel,
    SandboxRequirement,
    ToolRegistry,
    REGISTRY,
)


class ConnectorAuth(str, Enum):
    NONE = "none"
    USER_AUTH = "user_auth"
    SERVICE_AUTH = "service_auth"
    ELEVATED_AUTH = "elevated_auth"


class CredentialHandling(str, Enum):
    NONE = "none"
    REFERENCE_ONLY = "reference_only"
    PROVIDER_MANAGED_REFERENCE = "provider_managed_reference"


ConnectorSchema = Mapping[str, object]
SUPPORTED_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean"})
SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ConnectorSpec:
    """Declarative connector metadata; registration grants no execution authority."""

    identity: str
    description: str
    category: str
    capabilities: tuple[str, ...]
    scopes: tuple[str, ...]
    authentication_method: ConnectorAuth
    credential_handling: CredentialHandling
    network_requirement: NetworkRequirement
    read_write_mode: ReadWriteMode
    risk: RiskLevel
    approval_requirement: ApprovalRequirement
    sandbox_requirement: SandboxRequirement
    audit_requirement: AuditRequirement
    registered_tools: tuple[str, ...]
    enabled: bool
    version: str = "1.0"
    schema_version: str = SCHEMA_VERSION
    input_schema: ConnectorSchema = None  # type: ignore[assignment]
    output_schema: ConnectorSchema = None  # type: ignore[assignment]


def _schema() -> ConnectorSchema:
    return {"type": "object", "properties": {}, "additionalProperties": True}


BUILTIN_CONNECTORS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec("github", "Repository inspection connector.", "github", (Capability.INSPECT.value,), ("repository:read",), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.READ_ONLY, RiskLevel.LOW, ApprovalRequirement.NONE, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("github.inspect",), True, input_schema=_schema(), output_schema=_schema()),
    ConnectorSpec("web_research", "Explicit-resource web research connector.", "web", (Capability.NETWORK.value,), ("explicit-resource",), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.READ_ONLY, RiskLevel.MEDIUM, ApprovalRequirement.EXPLICIT, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("network.fetch",), True, input_schema=_schema(), output_schema=_schema()),
    ConnectorSpec("files", "Approved workspace file connector.", "files", (Capability.READ_FILE.value,), ("approved-workspace",), ConnectorAuth.NONE, CredentialHandling.NONE, NetworkRequirement.NONE, ReadWriteMode.READ_ONLY, RiskLevel.LOW, ApprovalRequirement.NONE, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("filesystem.read",), True, input_schema=_schema(), output_schema=_schema()),
    ConnectorSpec("ai_providers", "Configured free-provider benchmark connector.", "ai_provider", (Capability.BENCHMARK.value,), ("configured-free-provider",), ConnectorAuth.SERVICE_AUTH, CredentialHandling.PROVIDER_MANAGED_REFERENCE, NetworkRequirement.REQUIRED, ReadWriteMode.READ_ONLY, RiskLevel.MEDIUM, ApprovalRequirement.NONE, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("model.benchmark",), True, input_schema=_schema(), output_schema=_schema()),
    ConnectorSpec("project_repository", "Approval-gated repository inspection and change connector.", "github", (Capability.INSPECT.value, Capability.SOURCE_WRITE.value), ("repository:read", "reviewed-change"), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.CONTROLLED_WRITE, RiskLevel.HIGH, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("github.inspect", "github.change"), True, input_schema=_schema(), output_schema=_schema()),
    ConnectorSpec("email", "Future email connector; unavailable until registered tools exist.", "email", ("email.read", "email.send"), ("mailbox:read", "mailbox:send"), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.CONTROLLED_WRITE, RiskLevel.HIGH, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, (), False, input_schema=_schema(), output_schema=_schema()),
    ConnectorSpec("calendar_api", "Future calendar connector; unavailable until registered tools exist.", "calendar", ("calendar.read", "calendar.write"), ("calendar:read", "calendar:write"), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.CONTROLLED_WRITE, RiskLevel.HIGH, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, (), False, input_schema=_schema(), output_schema=_schema()),
    ConnectorSpec("browser", "Future controlled browser connector; unavailable until registered tools exist.", "browser", ("browser.automation",), ("explicit-site",), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.CONTROLLED_WRITE, RiskLevel.CRITICAL, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, (), False, input_schema=_schema(), output_schema=_schema()),
)


class ConnectorRegistryError(ValueError):
    pass


def _validate_schema(schema: ConnectorSchema, field: str) -> None:
    if not isinstance(schema, Mapping) or schema.get("type") not in SUPPORTED_SCHEMA_TYPES:
        raise ConnectorRegistryError(f"{field} must be a structured schema with a supported type")


def _validate_connector_spec(spec: ConnectorSpec, tool_registry: ToolRegistry) -> None:
    if not isinstance(spec, ConnectorSpec):
        raise ConnectorRegistryError("connector registration must use ConnectorSpec")
    if not spec.identity or spec.identity != spec.identity.strip().lower() or " " in spec.identity:
        raise ConnectorRegistryError("connector identity must be non-empty, normalized, and contain no spaces")
    if not isinstance(spec.description, str) or not spec.description.strip() or not isinstance(spec.category, str) or not spec.category.strip():
        raise ConnectorRegistryError("connector description and category are required")
    if not spec.capabilities or len(set(spec.capabilities)) != len(spec.capabilities):
        raise ConnectorRegistryError("connector capabilities must be non-empty and unique")
    if not spec.scopes or len(set(spec.scopes)) != len(spec.scopes):
        raise ConnectorRegistryError("connector scopes must be non-empty and unique")
    if not spec.registered_tools and spec.enabled:
        raise ConnectorRegistryError("enabled connector must expose at least one registered tool")
    if not isinstance(spec.authentication_method, ConnectorAuth):
        raise ConnectorRegistryError("connector authentication_method must use ConnectorAuth")
    if not isinstance(spec.credential_handling, CredentialHandling):
        raise ConnectorRegistryError("connector credential_handling must use CredentialHandling")
    for field in ("network_requirement", "read_write_mode", "risk", "approval_requirement", "sandbox_requirement", "audit_requirement"):
        if not isinstance(getattr(spec, field), Enum):
            raise ConnectorRegistryError(f"{field} must use its registry enum")
    if not isinstance(spec.version, str) or not spec.version.strip() or not isinstance(spec.schema_version, str) or not spec.schema_version.strip():
        raise ConnectorRegistryError("connector version and schema_version are required")
    if spec.schema_version != SCHEMA_VERSION:
        raise ConnectorRegistryError("unsupported connector schema version")
    _validate_schema(spec.input_schema, "input_schema")
    _validate_schema(spec.output_schema, "output_schema")
    if spec.authentication_method is ConnectorAuth.NONE and spec.credential_handling is not CredentialHandling.NONE:
        raise ConnectorRegistryError("unauthenticated connectors cannot declare credential handling")
    if spec.authentication_method is not ConnectorAuth.NONE and spec.credential_handling is CredentialHandling.NONE:
        raise ConnectorRegistryError("authenticated connectors require reference-only credential handling")

    for tool_name in spec.registered_tools:
        tool = tool_registry.get(tool_name)
        if tool is None:
            raise ConnectorRegistryError(f"connector references unknown registered tool: {tool_name}")
        if tool.capability not in spec.capabilities:
            raise ConnectorRegistryError("connector capability does not match registered tool capability")
        if tool.network_requirement is not spec.network_requirement:
            raise ConnectorRegistryError("connector network requirement does not exactly match tool contract")
        if tool.read_write_mode is not spec.read_write_mode:
            raise ConnectorRegistryError("connector read/write mode does not exactly match tool contract")
        if tool.risk_level is not spec.risk:
            raise ConnectorRegistryError("connector risk does not exactly match tool contract")
        if tool.approval_requirement is not spec.approval_requirement:
            raise ConnectorRegistryError("connector approval requirement does not exactly match tool contract")
        if tool.sandbox_requirement is not spec.sandbox_requirement:
            raise ConnectorRegistryError("connector sandbox requirement does not exactly match tool contract")
        if tool.audit_requirement is not spec.audit_requirement:
            raise ConnectorRegistryError("connector audit requirement does not exactly match tool contract")
        if ConnectorAuth(tool.authentication_requirement.value) is not spec.authentication_method:
            raise ConnectorRegistryError("connector authentication does not match tool authentication requirement")


class ConnectorRegistry:
    """Connector catalog whose authority remains entirely in ToolRegistry/capability policy."""

    def __init__(self, specs: Iterable[ConnectorSpec] = BUILTIN_CONNECTORS, *, tool_registry: ToolRegistry = REGISTRY):
        self._tool_registry = tool_registry
        self._connectors: dict[str, ConnectorSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ConnectorSpec) -> ConnectorSpec:
        _validate_connector_spec(spec, self._tool_registry)
        if spec.identity in self._connectors:
            raise ConnectorRegistryError(f"connector already registered: {spec.identity}")
        self._connectors[spec.identity] = spec
        return spec

    def get(self, identity: str) -> ConnectorSpec | None:
        if not isinstance(identity, str):
            return None
        return self._connectors.get(identity.strip().lower())

    def list(self) -> tuple[ConnectorSpec, ...]:
        return tuple(self._connectors[key] for key in sorted(self._connectors))

    def resolve_tool(self, identity: str, tool_name: str) -> object | None:
        spec = self.get(identity)
        if spec is None or not spec.enabled or tool_name not in spec.registered_tools:
            return None
        return self._tool_registry.get(tool_name)

    def authorize(
        self,
        identity: str,
        granted: Iterable[Capability | str] = (),
        *,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
        registry: ToolRegistry | None = None,
    ) -> CapabilityDecision:
        spec = self.get(identity)
        if spec is None:
            return CapabilityDecision(False, "connector is not registered", "")
        if not spec.enabled:
            return CapabilityDecision(False, "connector is disabled", "")
        if not spec.registered_tools:
            return CapabilityDecision(False, "connector has no registered executable tool", "")
        active_registry = registry or self._tool_registry
        try:
            _validate_connector_spec(spec, active_registry)
        except ConnectorRegistryError as exc:
            return CapabilityDecision(False, f"connector/tool contract mismatch: {exc}", "")
        for tool_name in spec.registered_tools:
            decision = active_registry.authorize(
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
