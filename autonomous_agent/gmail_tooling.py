from __future__ import annotations

from .capability_policy import Capability
from .tool_registry import (ApprovalRequirement, AuditRequirement, AuthenticationRequirement, NetworkRequirement, ReadWriteMode, RiskLevel, SandboxRequirement, ToolRegistry, ToolSpec)

_EMAIL_INPUT = {"type": "object", "properties": {"query": {"type": "string"}, "message_id": {"type": "string"}, "thread_id": {"type": "string"}, "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "idempotency_key": {"type": "string"}}, "additionalProperties": False}

GMAIL_TOOLS = (
    ToolSpec("email.search", "Bounded Gmail message search through the official API adapter.", "email", RiskLevel.MEDIUM, ReadWriteMode.READ_ONLY, NetworkRequirement.REQUIRED, AuthenticationRequirement.USER_AUTH, ApprovalRequirement.NONE, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, _EMAIL_INPUT, {"type": "object", "additionalProperties": True}, Capability.EMAIL.value, True),
    ToolSpec("email.read", "Read one bounded Gmail message with safe MIME handling.", "email", RiskLevel.MEDIUM, ReadWriteMode.READ_ONLY, NetworkRequirement.REQUIRED, AuthenticationRequirement.USER_AUTH, ApprovalRequirement.NONE, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, _EMAIL_INPUT, {"type": "object", "additionalProperties": True}, Capability.EMAIL.value, True),
    ToolSpec("email.thread", "Read one bounded Gmail thread in deterministic order.", "email", RiskLevel.MEDIUM, ReadWriteMode.READ_ONLY, NetworkRequirement.REQUIRED, AuthenticationRequirement.USER_AUTH, ApprovalRequirement.NONE, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, _EMAIL_INPUT, {"type": "object", "additionalProperties": True}, Capability.EMAIL.value, True),
    ToolSpec("email.draft", "Create an unsent Gmail draft; never sends automatically.", "email", RiskLevel.MEDIUM, ReadWriteMode.CONTROLLED_WRITE, NetworkRequirement.REQUIRED, AuthenticationRequirement.USER_AUTH, ApprovalRequirement.EXPLICIT, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, _EMAIL_INPUT, {"type": "object", "additionalProperties": True}, Capability.EMAIL.value, False),
    ToolSpec("email.send", "Send a Gmail message only after explicit human approval and deterministic idempotency validation.", "email", RiskLevel.CRITICAL, ReadWriteMode.HIGH_RISK_WRITE, NetworkRequirement.REQUIRED, AuthenticationRequirement.USER_AUTH, ApprovalRequirement.HUMAN_REVIEW, SandboxRequirement.REQUIRED, AuditRequirement.REQUIRED, _EMAIL_INPUT, {"type": "object", "additionalProperties": True}, Capability.EMAIL.value, False),
)

def register_gmail_tools(registry: ToolRegistry) -> tuple[ToolSpec, ...]:
    registered = []
    for tool in GMAIL_TOOLS:
        if registry.get(tool.name) is None:
            registered.append(registry.register(tool))
    return tuple(registered)
