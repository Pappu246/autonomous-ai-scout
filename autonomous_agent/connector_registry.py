from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Mapping
from .capability_policy import Capability,CapabilityDecision
from .tool_registry import ApprovalRequirement,AuditRequirement,NetworkRequirement,ReadWriteMode,RiskLevel,SandboxRequirement,ToolRegistry,REGISTRY
class ConnectorAuth(str,Enum): NONE="none";USER_AUTH="user_auth";SERVICE_AUTH="service_auth";ELEVATED_AUTH="elevated_auth"
class CredentialHandling(str,Enum): NONE="none";REFERENCE_ONLY="reference_only";PROVIDER_MANAGED_REFERENCE="provider_managed_reference"
@dataclass(frozen=True)
class ConnectorSpec:
    identity:str;description:str;category:str;capabilities:tuple[str,...];scopes:tuple[str,...];authentication_method:ConnectorAuth;credential_handling:CredentialHandling;network_requirement:NetworkRequirement;read_write_mode:ReadWriteMode;risk:RiskLevel;approval_requirement:ApprovalRequirement;sandbox_requirement:SandboxRequirement;audit_requirement:AuditRequirement;registered_tools:tuple[str,...];enabled:bool;version:str="1.0";schema_version:str="1.0";input_schema:Mapping[str,object]|None=None;output_schema:Mapping[str,object]|None=None
class ConnectorRegistryError(ValueError):pass
def _schema(s):return isinstance(s,Mapping) and s.get("type") in {"object","array","string","number","integer","boolean"}
def _validate(spec,tools):
    if not isinstance(spec,ConnectorSpec) or not spec.identity or spec.identity!=spec.identity.strip().lower() or not spec.capabilities or not spec.scopes:raise ConnectorRegistryError("invalid connector identity/capability/scope")
    if spec.enabled and not spec.registered_tools:raise ConnectorRegistryError("enabled connector must expose a registered tool")
    if spec.schema_version!="1.0" or not _schema(spec.input_schema) or not _schema(spec.output_schema):raise ConnectorRegistryError("invalid connector schema")
    if spec.authentication_method is ConnectorAuth.NONE and spec.credential_handling is not CredentialHandling.NONE:raise ConnectorRegistryError("unauthenticated connector cannot handle credentials")
    if spec.authentication_method is not ConnectorAuth.NONE and spec.credential_handling is CredentialHandling.NONE:raise ConnectorRegistryError("authenticated connector requires reference-only credentials")
    rank={RiskLevel.LOW:0,RiskLevel.MEDIUM:1,RiskLevel.HIGH:2,RiskLevel.CRITICAL:3}
    for name in spec.registered_tools:
        tool=tools.get(name)
        if tool is None:raise ConnectorRegistryError(f"unknown registered tool: {name}")
        if tool.capability not in spec.capabilities or tool.sandbox_requirement is not spec.sandbox_requirement or tool.audit_requirement is not spec.audit_requirement or tool.authentication_requirement.value!=spec.authentication_method.value:raise ConnectorRegistryError("connector/tool contract mismatch")
        if spec.network_requirement is NetworkRequirement.NONE and tool.network_requirement is not NetworkRequirement.NONE:raise ConnectorRegistryError("connector network contract mismatch")
        if rank.get(tool.risk_level,99)>rank.get(spec.risk,99):raise ConnectorRegistryError("connector risk contract is too weak")
        if spec.read_write_mode is ReadWriteMode.READ_ONLY and tool.read_write_mode is not ReadWriteMode.READ_ONLY:raise ConnectorRegistryError("read-only connector cannot expose writes")
        if spec.approval_requirement is ApprovalRequirement.NONE and tool.approval_requirement is not ApprovalRequirement.NONE:raise ConnectorRegistryError("connector cannot hide approval requirement")
class ConnectorRegistry:
    def __init__(self,specs=(),*,tool_registry:ToolRegistry=REGISTRY):self._tools=tool_registry;self._items={};[self.register(s) for s in specs]
    def register(self,spec):_validate(spec,self._tools);self._items.setdefault(spec.identity,spec);return spec
    def get(self,identity):return self._items.get(identity.strip().lower()) if isinstance(identity,str) else None
    def list(self):return tuple(self._items[k] for k in sorted(self._items))
    def resolve_tool(self,identity,tool_name):
        spec=self.get(identity);return self._tools.get(tool_name) if spec and spec.enabled and tool_name in spec.registered_tools else None
    def authorize(self,identity,granted=(),**kwargs):
        spec=self.get(identity)
        if spec is None:return CapabilityDecision(False,"connector is not registered","")
        if not spec.enabled:return CapabilityDecision(False,"connector is disabled","")
        try:_validate(spec,self._tools)
        except ConnectorRegistryError as exc:return CapabilityDecision(False,f"connector/tool contract mismatch: {exc}","")
        for name in spec.registered_tools:
            d=self._tools.authorize(name,granted,**kwargs)
            if not d.allowed:return d
        return CapabilityDecision(True,"connector is permitted by existing Tool Registry and capability policy",spec.registered_tools[0])
def web_connector(tool_registry=REGISTRY):
    schema={"type":"object","additionalProperties":True};spec=ConnectorSpec("web_research","Bounded public web research connector","web",(Capability.WEB_RESEARCH.value,), ("public:read",),ConnectorAuth.NONE,CredentialHandling.NONE,NetworkRequirement.REQUIRED,ReadWriteMode.READ_ONLY,RiskLevel.MEDIUM,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,("web.search","web.read","web.extract","web.compare"),True,input_schema=schema,output_schema=schema);return ConnectorRegistry((spec,),tool_registry=tool_registry)
def filesystem_connector(tool_registry=REGISTRY):
    schema={"type":"object","additionalProperties":True};read=ConnectorSpec("filesystem_workspace_read","Bounded workspace filesystem reads","files",(Capability.FILES_WORKSPACE.value,), ("workspace:read",),ConnectorAuth.NONE,CredentialHandling.NONE,NetworkRequirement.NONE,ReadWriteMode.READ_ONLY,RiskLevel.LOW,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,("filesystem.read","filesystem.list"),True,input_schema=schema,output_schema=schema);write=ConnectorSpec("filesystem_workspace_write","Approval-gated workspace filesystem writes","files",(Capability.FILES_WORKSPACE.value,),("workspace:write",),ConnectorAuth.NONE,CredentialHandling.NONE,NetworkRequirement.NONE,ReadWriteMode.CONTROLLED_WRITE,RiskLevel.HIGH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,("filesystem.write","filesystem.transform"),True,input_schema=schema,output_schema=schema);return ConnectorRegistry((read,write),tool_registry=tool_registry)
def gmail_connector(tool_registry=REGISTRY,*,enabled=False):
    from .gmail_tooling import register_gmail_tools
    register_gmail_tools(tool_registry)
    schema={"type":"object","additionalProperties":True}
    spec=ConnectorSpec("gmail","Official Gmail REST/OAuth connector; disabled until a legitimate OAuth connection is configured","email",(Capability.EMAIL.value,),("gmail.readonly","gmail.compose","gmail.send"),ConnectorAuth.USER_AUTH,CredentialHandling.REFERENCE_ONLY,NetworkRequirement.REQUIRED,ReadWriteMode.CONTROLLED_WRITE,RiskLevel.CRITICAL,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,("email.search","email.read","email.thread","email.draft","email.send"),enabled,input_schema=schema,output_schema=schema)
    return ConnectorRegistry((spec,),tool_registry=tool_registry)
