from __future__ import annotations
from .capability_policy import Capability
from .tool_registry import ApprovalRequirement,AuditRequirement,AuthenticationRequirement,NetworkRequirement,ReadWriteMode,RiskLevel,SandboxRequirement,ToolRegistry,ToolSpec
_INPUT={"type":"object","properties":{"calendar_id":{"type":"string"},"event_id":{"type":"string"},"time_min":{"type":"string"},"time_max":{"type":"string"},"query":{"type":"string"},"results":{"type":"integer"},"duration_minutes":{"type":"integer"},"event":{"type":"object"},"etag":{"type":"string"},"idempotency_key":{"type":"string"}},"additionalProperties":False}
CALENDAR_TOOLS=(
 ToolSpec("calendar.read","Read one bounded calendar event through the official API adapter.","calendar",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,_INPUT,{"type":"object","additionalProperties":True},Capability.CALENDAR.value,True),
 ToolSpec("calendar.list","List bounded calendar events with timezone-aware bounds.","calendar",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,_INPUT,{"type":"object","additionalProperties":True},Capability.CALENDAR.value,True),
 ToolSpec("calendar.find_free_time","Find bounded free slots without modifying a calendar.","calendar",RiskLevel.LOW,ReadWriteMode.READ_ONLY,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.NONE,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,_INPUT,{"type":"object","additionalProperties":True},Capability.CALENDAR.value,True),
 ToolSpec("calendar.event.create","Create a calendar event only after explicit approval.","calendar",RiskLevel.HIGH,ReadWriteMode.CONTROLLED_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,_INPUT,{"type":"object","additionalProperties":True},Capability.CALENDAR.value,False),
 ToolSpec("calendar.event.update","Update a calendar event only after approval and stale-event validation.","calendar",RiskLevel.HIGH,ReadWriteMode.CONTROLLED_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,_INPUT,{"type":"object","additionalProperties":True},Capability.CALENDAR.value,False),
 ToolSpec("calendar.event.cancel","Cancel a calendar event only after approval and etag validation.","calendar",RiskLevel.CRITICAL,ReadWriteMode.HIGH_RISK_WRITE,NetworkRequirement.REQUIRED,AuthenticationRequirement.USER_AUTH,ApprovalRequirement.HUMAN_REVIEW,SandboxRequirement.REQUIRED,AuditRequirement.REQUIRED,_INPUT,{"type":"object","additionalProperties":True},Capability.CALENDAR.value,False),)
def register_calendar_tools(registry:ToolRegistry)->tuple[ToolSpec,...]:
    out=[]
    for tool in CALENDAR_TOOLS:
        if registry.get(tool.name) is None:out.append(registry.register(tool))
    return tuple(out)
