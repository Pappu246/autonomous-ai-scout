from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .capability_policy import Capability
from .tool_registry import REGISTRY, ToolRegistry


class ConnectorAuth(str, Enum):
    NONE = "none"
    USER_AUTH = "user_auth"


class CredentialHandling(str, Enum):
    NONE = "none"
    REFERENCE_ONLY = "reference_only"


class NetworkRequirement(str, Enum):
    NONE = "none"
    REQUIRED = "required"


class ReadWriteMode(str, Enum):
    READ_ONLY = "read_only"
    CONTROLLED_WRITE = "controlled_write"
    HIGH_RISK_WRITE = "high_risk_write"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalRequirement(str, Enum):
    NONE = "none"
    EXPLICIT = "explicit"
    HUMAN_REVIEW = "human_review"


class SandboxRequirement(str, Enum):
    NONE = "none"
    REQUIRED = "required"


class AuditRequirement(str, Enum):
    REQUIRED = "required"


class ConnectorRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ConnectorSpec:
    connector_id: str
    description: str
    domain: str
    capabilities: tuple[str, ...]
    scopes: tuple[str, ...]
    auth: ConnectorAuth
    credential_handling: CredentialHandling
    network_requirement: NetworkRequirement
    read_write_mode: ReadWriteMode
    risk_level: RiskLevel
    approval_requirement: ApprovalRequirement
    sandbox_requirement: SandboxRequirement
    audit_requirement: AuditRequirement
    tool_names: tuple[str, ...]
    enabled: bool = True
    input_schema: Mapping[str, object] | None = None
    output_schema: Mapping[str, object] | None = None

    @property
    def capability(self) -> str:
        return self.capabilities[0] if self.capabilities else ""

    def authorize(self, tool_name: str, *, explicitly_approved: bool = False, registry: ToolRegistry | None = None) -> bool:
        registry = registry or REGISTRY
        if not self.enabled or tool_name not in self.tool_names:
            return False
        spec = registry.get(tool_name)
        if spec is None:
            return False
        if spec.capability not in self.capabilities:
            return False
        if spec.approval_requirement is ApprovalRequirement.NONE:
            return True
        return explicitly_approved and spec.approval_requirement in {ApprovalRequirement.EXPLICIT, ApprovalRequirement.HUMAN_REVIEW}


class ConnectorRegistry:
    def __init__(self, specs: Iterable[ConnectorSpec], *, tool_registry: ToolRegistry = REGISTRY):
        specs = tuple(specs)
        self._tools = tool_registry
        self._specs = {spec.connector_id: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ConnectorRegistryError("duplicate connector id")
        for spec in self._specs.values():
            for capability in spec.capabilities:
                if capability not in {item.value for item in Capability}:
                    raise ConnectorRegistryError(f"unknown capability: {capability}")
                if any(self._tools.get(name) is None for name in spec.tool_names):
                    raise ConnectorRegistryError(f"connector tool is not registered: {spec.connector_id}")

    def get(self, connector_id: str) -> ConnectorSpec | None:
        return self._specs.get(connector_id)

    def all(self) -> tuple[ConnectorSpec, ...]:
        return tuple(self._specs.values())


def _schema() -> dict[str, object]:
    return {"type": "object", "additionalProperties": True}


def web_connector(tool_registry: ToolRegistry = REGISTRY):
    spec = ConnectorSpec("web", "Bounded public-web research connector", "web", (Capability.WEB_RESEARCH.value,), ("web:read",), ConnectorAuth.NONE, CredentialHandling.NONE, NetworkRequirement.REQUIRED, ReadWriteMode.READ_ONLY, RiskLevel.LOW, ApprovalRequirement.NONE, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("web.search", "web.read", "web.extract", "web.compare"), True, input_schema=_schema(), output_schema=_schema())
    return ConnectorRegistry((spec,), tool_registry=tool_registry)


def filesystem_connector(tool_registry: ToolRegistry = REGISTRY):
    spec = ConnectorSpec("filesystem_workspace", "Approval-gated workspace filesystem connector", "files", (Capability.FILES_WORKSPACE.value,), ("workspace:read", "workspace:write"), ConnectorAuth.NONE, CredentialHandling.NONE, NetworkRequirement.NONE, ReadWriteMode.CONTROLLED_WRITE, RiskLevel.HIGH, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("filesystem.list", "filesystem.read", "filesystem.write", "filesystem.transform"), True, input_schema=_schema(), output_schema=_schema())
    return ConnectorRegistry((spec,), tool_registry=tool_registry)


def gmail_connector(tool_registry: ToolRegistry = REGISTRY, *, enabled: bool = False):
    from .gmail_tooling import register_gmail_tools
    register_gmail_tools(tool_registry)
    spec = ConnectorSpec("gmail", "Official Gmail REST/OAuth connector; disabled until a legitimate OAuth connection is configured", "gmail", (Capability.EMAIL.value,), ("gmail.readonly", "gmail.compose", "gmail.send"), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.CONTROLLED_WRITE, RiskLevel.CRITICAL, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("email.search", "email.read", "email.thread", "email.draft", "email.send"), enabled, input_schema=_schema(), output_schema=_schema())
    return ConnectorRegistry((spec,), tool_registry=tool_registry)


def calendar_connector(tool_registry: ToolRegistry = REGISTRY, *, enabled: bool = False):
    from .calendar_tooling import register_calendar_tools
    register_calendar_tools(tool_registry)
    spec = ConnectorSpec("calendar", "Official Calendar REST/OAuth connector; disabled until a legitimate OAuth connection is configured", "calendar", (Capability.CALENDAR.value,), ("calendar.readonly", "calendar.events"), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.CONTROLLED_WRITE, RiskLevel.CRITICAL, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, ("calendar.read", "calendar.list", "calendar.find_free_time", "calendar.event.create", "calendar.event.update", "calendar.event.cancel"), enabled, input_schema=_schema(), output_schema=_schema())
    return ConnectorRegistry((spec,), tool_registry=tool_registry)


def rest_connector(tool_registry=REGISTRY, *, enabled=False, allowed_hosts=(), transport=None):
    from .rest_connector import RestConnector
    schema = _schema()
    tools = ("rest.get", "rest.head", "rest.write")
    spec = ConnectorSpec("generic_rest", "Bounded generic REST connector; disabled until explicit API host allowlisting and legitimate credentials are configured", "rest", (Capability.REST_API.value,), ("rest:read", "rest:write"), ConnectorAuth.USER_AUTH, CredentialHandling.REFERENCE_ONLY, NetworkRequirement.REQUIRED, ReadWriteMode.CONTROLLED_WRITE, RiskLevel.CRITICAL, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, tools, enabled, input_schema=schema, output_schema=schema)
    if any(tool_registry.get(name) is None for name in tools):
        if tool_registry is REGISTRY:
            raise ConnectorRegistryError("REST tools are not registered in the target registry")
        from .tool_registry import BUILTIN_TOOLS
        for builtin in BUILTIN_TOOLS:
            if builtin.name.startswith("rest.") and tool_registry.get(builtin.name) is None:
                tool_registry.register(builtin)
    connector = None
    if allowed_hosts:
        if transport is None:
            raise ConnectorRegistryError("REST connector requires an injected transport")
        connector = RestConnector(set(allowed_hosts), transport=transport)
    return ConnectorRegistry((spec,), tool_registry=tool_registry), connector
