from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Iterable,Mapping
from .capability_policy import Capability,CapabilityDecision,check_capability
class RiskLevel(str,Enum): LOW="low";MEDIUM="medium";HIGH="high";CRITICAL="critical"
class ReadWriteMode(str,Enum): READ_ONLY="read_only";SAFE_WRITE="safe_write";CONTROLLED_WRITE="controlled_write";HIGH_RISK_WRITE="high_risk_write"
class NetworkRequirement(str,Enum): NONE="none";OPTIONAL="optional";REQUIRED="required"
class AuthenticationRequirement(str,Enum): NONE="none";USER_AUTH="user_auth";SERVICE_AUTH="service_auth";ELEVATED_AUTH="elevated_auth"
class ApprovalRequirement(str,Enum): NONE="none";EXPLICIT="explicit";HUMAN_REVIEW="human_review"
class SandboxRequirement(str,Enum): NONE="none";REQUIRED="required"
class AuditRequirement(str,Enum): NONE="none";REQUIRED="required"
Schema=Mapping[str,object]
@dataclass(frozen=True)
class ToolSpec:
    name:str;description:str;category:str;risk_level:RiskLevel;read_write_mode:ReadWriteMode;network_requirement:NetworkRequirement;authentication_requirement:AuthenticationRequirement;approval_requirement:ApprovalRequirement;sandbox_requirement:SandboxRequirement;audit_requirement:AuditRequirement;input_schema:Schema;output_schema:Schema;capability:str;safe_autonomous:bool
_SAFE_INPUT={"type":"object","properties":{},"additionalProperties":True};_SAFE_OUTPUT={"type":"object","properties":{},"additionalProperties":True}
_WEB_INPUT={"type":"object","properties":{"query":{"type":"string"},"url":{"type":"string"},"urls":{"type":"array"},"fields":{"type":"array"}},"additionalProperties":False}
_FILES_INPUT={"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"},"find":{"type":"string"},"replace":{"type":"string"}},"additionalProperties":False}
_CALENDAR_INPUT={"type":"object","properties":{"calendar_id":{"type":"string"},"event_id":{"type":"string"},"time_min":{"type":"string"},"time_max":{"type":"string"},"query":{"type":"string"},"results":{"type":"integer"},"duration_minutes":{"type":"integer"},"event":{"type":"object"},"etag":{"type":"string"},"idempotency_key":{"type":"string"}},"additionalProperties":False}
_EMAIL_INPUT={"type":"object","properties":{"query":{"type":"string"},"message_id":{"type":"string"},"thread_id":{"type":"string"},"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"},"idempotency_key":{"type":"string"}},"additionalProperties":False}
def _tool(name,description,category,risk,mode,network,auth,approval,sandbox,audit,capability,*,safe_autonomous,input_schema=_SAFE_INPUT,output_schema=_SAFE_OUTPUT):return ToolSpec(name,description,category,risk,mode,network,auth,approval,sandbox,audit,input_schema,output_schema,capability.value,safe_autonomous)
BUILTIN_TOOLS=(
_tool("github.inspect","Read repository metadata and source state.","github",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.INSPECT,safe_autonomous=True),
_tool("filesystem.read","Read files from an approved workspace.","files",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.FILES_WORKSPACE,safe_autonomous=True,input_schema=_FILES_INPUT),
_tool("filesystem.list","Enumerate an approved workspace directory with strict bounds.","files",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.FILES_WORKSPACE,safe_autonomous=True,input_schema=_FILES_INPUT),
_tool("filesystem.write","Write only inside an approved workspace after explicit approval.","files",RiskLevel.HIGH,ReadWriteMode.CONTROLLED_WRITE,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.FILES_WORKSPACE,safe_autonomous=False,input_schema=_FILES_INPUT),
_tool("filesystem.transform","Transform an approved workspace file after explicit approval.","files",RiskLevel.HIGH,ReadWriteMode.CONTROLLED_WRITE,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.FILES_WORKSPACE,safe_autonomous=False,input_schema=_FILES_INPUT),
_tool("tests.run","Run the project's automated tests.","testing",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.TEST,safe_autonomous=True),
_tool("lint.run","Run configured static checks.","testing",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.LINT,safe_autonomous=True),
_tool("metrics.collect","Collect deterministic project metrics.","observability",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.METRICS,safe_autonomous=True),
_tool("model.benchmark","Benchmark an explicitly configured free model.","ai_provider",RiskLevel.MEDIUM,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.SERVICE_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.BENCHMARK,safe_autonomous=True),
_tool("network.fetch","Fetch an explicitly requested network resource.","web",RiskLevel.MEDIUM,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.EXPLICIT,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.NETWORK,safe_autonomous=False),
_tool("web.search","Bounded public web search through the controlled web connector.","web",RiskLevel.MEDIUM,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.WEB_RESEARCH,safe_autonomous=True,input_schema=_WEB_INPUT),
_tool("web.read","Read a bounded public web page with redirect and content validation.","web",RiskLevel.MEDIUM,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.WEB_RESEARCH,safe_autonomous=True,input_schema=_WEB_INPUT),
_tool("web.extract","Deterministically extract requested facts.","web",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.WEB_RESEARCH,safe_autonomous=True,input_schema=_WEB_INPUT),
_tool("web.compare","Compare retrieved public sources.","web",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.NONE,AuthenticationRequirement.NONE,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.WEB_RESEARCH,safe_autonomous=True,input_schema=_WEB_INPUT),
_tool("email.search","Bounded Gmail message search.","email",RiskLevel.MEDIUM,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.EMAIL,safe_autonomous=True,input_schema=_EMAIL_INPUT),
_tool("email.read","Read one bounded Gmail message.","email",RiskLevel.MEDIUM,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.EMAIL,safe_autonomous=True,input_schema=_EMAIL_INPUT),
_tool("email.thread","Read one bounded Gmail thread.","email",RiskLevel.MEDIUM,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.EMAIL,safe_autonomous=True,input_schema=_EMAIL_INPUT),
_tool("email.draft","Create an unsent Gmail draft.","email",RiskLevel.MEDIUM,ReadWriteMode.CONTROLLED_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.EXPLICIT,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.EMAIL,safe_autonomous=False,input_schema=_EMAIL_INPUT),
_tool("email.send","Send Gmail only after explicit human review.","email",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.EMAIL,safe_autonomous=False,input_schema=_EMAIL_INPUT),
_tool("calendar.read","Read one bounded calendar event.","calendar",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.CALENDAR,safe_autonomous=True,input_schema=_CALENDAR_INPUT),
_tool("calendar.list","List bounded calendar events.","calendar",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.CALENDAR,safe_autonomous=True,input_schema=_CALENDAR_INPUT),
_tool("calendar.find_free_time","Find free calendar slots without mutation.","calendar",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.CALENDAR,safe_autonomous=True,input_schema=_CALENDAR_INPUT),
_tool("calendar.event.create","Create an event only after human approval.","calendar",RiskLevel.HIGH,ReadWriteMode.CONTROLLED_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.CALENDAR,safe_autonomous=False,input_schema=_CALENDAR_INPUT),
_tool("calendar.event.update","Update an event only after human approval.","calendar",RiskLevel.HIGH,ReadWriteMode.CONTROLLED_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.CALENDAR,safe_autonomous=False,input_schema=_CALENDAR_INPUT),
_tool("calendar.event.cancel","Cancel an event only after human approval.","calendar",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.CALENDAR,safe_autonomous=False,input_schema=_CALENDAR_INPUT),
_tool("github.change","Prepare a source change for the existing approval-gated review boundary.","github",RiskLevel.HIGH,ReadWriteMode.CONTROLLED_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.SOURCE_WRITE,safe_autonomous=False),
_tool("github.merge","Merge a reviewed pull request.","github",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.MERGE,safe_autonomous=False),
_tool("production.deploy","Deploy software to production.","deployment",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.ELEVATED_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.DEPLOY,safe_autonomous=False),
_tool("billing.manage","Change billing or paid-provider state.","billing",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.ELEVATED_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.BILLING,safe_autonomous=False),
_tool("payment.manage","Perform payment operations.","payment",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.ELEVATED_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.BILLING,safe_autonomous=False),
_tool("secrets.manage","Change secret material.","secrets",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.OPTIONAL,AuthenticationRequirement.ELEVATED_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.SECRETS,safe_autonomous=False),
_tool("destructive.execute","Perform destructive actions.","destructive",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.OPTIONAL,AuthenticationRequirement.ELEVATED_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,Capability.DESTRUCTIVE,safe_autonomous=False),)
class ToolRegistryError(ValueError):pass
class DuplicateToolError(ToolRegistryError):pass
def _validate_schema(schema,field):
    if not isinstance(schema,Mapping) or schema.get("type") not in {"object","array","string","number","integer","boolean"}:raise ToolRegistryError(f"{field} must be a structured schema with a supported type")
def validate_tool_spec(spec):
    if not isinstance(spec,ToolSpec):raise ToolRegistryError("tool registration must use ToolSpec")
    if not spec.name or spec.name!=spec.name.strip().lower() or " " in spec.name:raise ToolRegistryError("tool name must be normalized")
    if not spec.description.strip() or not spec.category.strip():raise ToolRegistryError("tool description and category are required")
    for field in ("risk_level","read_write_mode","network_requirement","authentication_requirement","approval_requirement","sandbox_requirement","audit_requirement"):
        if not isinstance(getattr(spec,field),Enum):raise ToolRegistryError(f"{field} must use its registry enum")
    _validate_schema(spec.input_schema,"input_schema");_validate_schema(spec.output_schema,"output_schema")
    try:capability=Capability(spec.capability)
    except ValueError as exc:raise ToolRegistryError("unknown capability") from exc
    if spec.risk_level in {RiskLevel.HIGH,RiskLevel.CRITICAL} and spec.approval_requirement is ApprovalRequirement.NONE:raise ToolRegistryError("high-risk tools require approval")
    if spec.risk_level in {RiskLevel.HIGH,RiskLevel.CRITICAL} and spec.audit_requirement is not AuditRequirement.REQUIRED:raise ToolRegistryError("high-risk tools require audit")
    if spec.safe_autonomous and spec.approval_requirement is not ApprovalRequirement.NONE:raise ToolRegistryError("safe autonomous tools cannot require approval")
    if spec.safe_autonomous and spec.read_write_mode is not ReadWriteMode.READ_ONLY:raise ToolRegistryError("safe autonomous tools must be read-only")
    if capability in {Capability.SOURCE_WRITE,Capability.MERGE,Capability.DEPLOY,Capability.BILLING,Capability.SECRETS,Capability.DESTRUCTIVE} and (spec.safe_autonomous or spec.approval_requirement is ApprovalRequirement.NONE):raise ToolRegistryError("restricted capability requires approval")
    return spec
class ToolRegistry:
    def __init__(self,specs=BUILTIN_TOOLS):self._tools={};[self.register(s) for s in specs]
    def register(self,spec):
        validate_tool_spec(spec)
        if spec.name in self._tools:raise DuplicateToolError(f"tool already registered: {spec.name}")
        self._tools[spec.name]=spec;return spec
    def get(self,name):return self._tools.get(name.strip().lower()) if isinstance(name,str) else None
    def list(self):return tuple(self._tools.values())
    def authorize(self,name,granted=(),*,explicitly_approved=False,sandbox_available=True,audit_available=True):
        spec=self.get(name)
        if spec is None:return CapabilityDecision(False,"tool is not registered","")
        decision=check_capability(spec.capability,granted)
        if not decision.allowed:return decision
        if spec.sandbox_requirement is SandboxRequirement.REQUIRED and not sandbox_available:return CapabilityDecision(False,"tool requires an available sandbox",spec.capability)
        if spec.audit_requirement is AuditRequirement.REQUIRED and not audit_available:return CapabilityDecision(False,"tool requires an available audit boundary",spec.capability)
        if spec.approval_requirement is not ApprovalRequirement.NONE and not explicitly_approved:return CapabilityDecision(False,"tool requires explicit approval",spec.capability)
        return CapabilityDecision(True,"tool is permitted by capability policy and registry contract",spec.capability)
REGISTRY=ToolRegistry()
def list_tools():return REGISTRY.list()
def get_tool(name):return REGISTRY.get(name)
def register_tool(spec):return REGISTRY.register(spec)
def authorize_tool(name,granted=(),*,explicitly_approved=False,sandbox_available=True,audit_available=True):return REGISTRY.authorize(name,granted,explicitly_approved=explicitly_approved,sandbox_available=sandbox_available,audit_available=audit_available)
