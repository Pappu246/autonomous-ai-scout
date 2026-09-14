from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .capability_policy import Capability, CapabilityDecision
from .tool_registry import ApprovalRequirement, AuditRequirement, NetworkRequirement, ReadWriteMode, RiskLevel, SandboxRequirement, ToolRegistry, REGISTRY

class ConnectorAuth(str, Enum):
    NONE="none"; USER_AUTH="user_auth"; SERVICE_AUTH="service_auth"; ELEVATED_AUTH="elevated_auth"
class CredentialHandling(str, Enum):
    NONE="none"; REFERENCE_ONLY="reference_only"; PROVIDER_MANAGED_REFERENCE="provider_managed_reference"
ConnectorSchema=Mapping[str,object]
SUPPORTED_SCHEMA_TYPES=frozenset({"object","array","string","number","integer","boolean"})
SCHEMA_VERSION="1.0"

@dataclass(frozen=True)
class ConnectorSpec:
    identity:str; description:str; category:str; capabilities:tuple[str,...]; scopes:tuple[str,...]
    authentication_method:ConnectorAuth; credential_handling:CredentialHandling
    network_requirement:NetworkRequirement; read_write_mode:ReadWriteMode; risk:RiskLevel
    approval_requirement:ApprovalRequirement; sandbox_requirement:SandboxRequirement; audit_requirement:AuditRequirement
    registered_tools:tuple[str,...]; enabled:bool; version:str="1.0"; schema_version:str=SCHEMA_VERSION
    input_schema:ConnectorSchema|None=None; output_schema:ConnectorSchema|None=None

class ConnectorRegistryError(ValueError): pass

def _schema()->ConnectorSchema:return {"type":"object","properties":{},"additionalProperties":True}
def _validate_schema(schema:ConnectorSchema|None,field:str)->None:
    if not isinstance(schema,Mapping) or schema.get("type") not in SUPPORTED_SCHEMA_TYPES: raise ConnectorRegistryError(f"{field} must be a structured schema with a supported type")

def _validate_connector_spec(spec:ConnectorSpec,tools:ToolRegistry)->None:
    if not isinstance(spec,ConnectorSpec) or not spec.identity or spec.identity!=spec.identity.strip().lower() or " " in spec.identity: raise ConnectorRegistryError("connector identity invalid")
    if not spec.description.strip() or not spec.category.strip() or not spec.capabilities or not spec.scopes: raise ConnectorRegistryError("connector metadata incomplete")
    if spec.enabled and not spec.registered_tools: raise ConnectorRegistryError("enabled connector must expose at least one registered tool")
    if spec.schema_version!=SCHEMA_VERSION: raise ConnectorRegistryError("unsupported connector schema version")
    _validate_schema(spec.input_schema,"input_schema"); _validate_schema(spec.output_schema,"output_schema")
    if spec.authentication_method is ConnectorAuth.NONE and spec.credential_handling is not CredentialHandling.NONE: raise ConnectorRegistryError("unauthenticated connector cannot handle credentials")
    if spec.authentication_method is not ConnectorAuth.NONE and spec.credential_handling is CredentialHandling.NONE: raise ConnectorRegistryError("authenticated connector requires reference-only credential handling")
    rank={RiskLevel.LOW:0,RiskLevel.MEDIUM:1,RiskLevel.HIGH:2,RiskLevel.CRITICAL:3}
    for name in spec.registered_tools:
        tool=tools.get(name)
        if tool is None: raise ConnectorRegistryError(f"unknown registered tool: {name}")
        if tool.capability not in spec.capabilities: raise ConnectorRegistryError("connector capability mismatch")
        if tool.network_requirement is not spec.network_requirement: raise ConnectorRegistryError("connector network requirement mismatch")
        if tool.read_write_mode is not spec.read_write_mode: raise ConnectorRegistryError("connector read/write mode mismatch")
        if tool.risk_level is not spec.risk: raise ConnectorRegistryError("connector risk mismatch")
        if tool.approval_requirement is not spec.approval_requirement: raise ConnectorRegistryError("connector approval requirement mismatch")
        if tool.sandbox_requirement is not spec.sandbox_requirement: raise ConnectorRegistryError("connector sandbox requirement mismatch")
        if tool.audit_requirement is not spec.audit_requirement: raise ConnectorRegistryError("connector audit requirement mismatch")
        if tool.authentication_requirement.value!=spec.authentication_method.value: raise ConnectorRegistryError("connector authentication mismatch")

class ConnectorRegistry:
    def __init__(self,specs:Iterable[ConnectorSpec]=(),*,tool_registry:ToolRegistry=REGISTRY):
        self._tools=tool_registry; self._items={}; [self.register(s) for s in specs]
    def register(self,spec):
        _validate_connector_spec(spec,self._tools)
        if spec.identity in self._items: raise ConnectorRegistryError(f"connector already registered: {spec.identity}")
        self._items[spec.identity]=spec; return spec
    def get(self,identity): return self._items.get(identity.strip().lower()) if isinstance(identity,str) else None
    def list(self): return tuple(self._items[k] for k in sorted(self._items))
    def resolve_tool(self,identity,tool_name):
        spec=self.get(identity); return self._tools.get(tool_name) if spec and spec.enabled and tool_name in spec.registered_tools else None
    def authorize(self,identity,granted=(),*,explicitly_approved=False,sandbox_available=True,audit_available=True,registry=None):
        spec=self.get(identity)
        if spec is None: return CapabilityDecision(False,"connector is not registered","")
        if not spec.enabled: return CapabilityDecision(False,"connector is disabled","")
        try: _validate_connector_spec(spec,self._tools)
        except ConnectorRegistryError as exc: return CapabilityDecision(False,f"connector/tool contract mismatch: {exc}","")
        tools=registry or self._tools
        for name in spec.registered_tools:
            d=tools.authorize(name,granted,explicitly_approved=explicitly_approved,sandbox_available=sandbox_available,audit_available=audit_available)
            if not d.allowed: return d
        return CapabilityDecision(True,"connector is permitted by existing Tool Registry and capability policy",spec.registered_tools[0])

def _make_connector(identity, description, category, capability, scopes, auth, credentials, network, mode, risk, approval, sandbox, audit, tools, enabled=True, registered_tools=()):
    schema=_schema()
    return ConnectorRegistry((ConnectorSpec(identity,description,category,(capability.value,),tuple(scopes),auth,credentials,network,mode,risk,approval,sandbox,audit,tuple(registered_tools),enabled,input_schema=schema,output_schema=schema),),tool_registry=tools)

def web_connector(tool_registry=REGISTRY):
    return _make_connector("web_research","Bounded public web research connector","web",Capability.WEB_RESEARCH,("public:read",),ConnectorAuth.NONE,CredentialHandling.NONE,NetworkRequirement.REQUIRED,ReadWriteMode.READ_ONLY,RiskLevel.MEDIUM,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,tool_registry,registered_tools=("web.search","web.read","web.extract","web.compare"))

def filesystem_connector(tool_registry=REGISTRY):
    schema=_schema()
    read=ConnectorSpec("filesystem_workspace_read","Bounded workspace filesystem reads","files",(Capability.FILES_WORKSPACE.value,), ("workspace:read",),ConnectorAuth.NONE,CredentialHandling.NONE,NetworkRequirement.NONE,ReadWriteMode.READ_ONLY,RiskLevel.LOW,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,("filesystem.read","filesystem.list"),True,input_schema=schema,output_schema=schema)
    write=ConnectorSpec("filesystem_workspace_write","Approval-gated workspace filesystem writes","files",(Capability.FILES_WORKSPACE.value,), ("workspace:write",),ConnectorAuth.NONE,CredentialHandling.NONE,NetworkRequirement.NONE,ReadWriteMode.CONTROLLED_WRITE,RiskLevel.HIGH,ApprovalRequirement.EXPLICIT,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,("filesystem.write","filesystem.transform"),True,input_schema=schema,output_schema=schema)
    return ConnectorRegistry((read,write),tool_registry=tool_registry)

def gmail_connector(tool_registry=REGISTRY,*,enabled=False):
    from .gmail_tooling import register_gmail_tools
    register_gmail_tools(tool_registry)
    return _make_connector("gmail","Official Gmail REST/OAuth connector; disabled until a legitimate OAuth connection is configured","email",Capability.EMAIL,("gmail.readonly","gmail.compose","gmail.send"),ConnectorAuth.USER_AUTH,CredentialHandling.REFERENCE_ONLY,NetworkRequirement.REQUIRED,ReadWriteMode.CONTROLLED_WRITE,RiskLevel.CRITICAL,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,tool_registry,enabled, ("email.search","email.read","email.thread","email.draft","email.send"))

def calendar_connector(tool_registry=REGISTRY,*,enabled=False):
    from .calendar_tooling import register_calendar_tools
    register_calendar_tools(tool_registry)
    return _make_connector("calendar","Official Calendar REST/OAuth connector; disabled until a legitimate OAuth connection is configured","calendar",Capability.CALENDAR,("calendar.readonly","calendar.events"),ConnectorAuth.USER_AUTH,CredentialHandling.REFERENCE_ONLY,NetworkRequirement.REQUIRED,ReadWriteMode.CONTROLLED_WRITE,RiskLevel.CRITICAL,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,tool_registry,enabled,("calendar.read","calendar.list","calendar.find_free_time","calendar.event.create","calendar.event.update","calendar.event.cancel"))
