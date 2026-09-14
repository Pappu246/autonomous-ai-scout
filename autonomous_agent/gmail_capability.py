from __future__ import annotations
from .capability_policy import Capability
from .universal_capability import CapabilityRegistry,CapabilitySpec,Domain,IdempotencyMode,RetryPolicy

def gmail_capabilities(tool_registry):
    registry=CapabilityRegistry(tool_registry)
    schema={"type":"object","additionalProperties":True}
    specs=(
        CapabilitySpec("email:search",Domain.EMAIL,"email.search",schema,schema,"medium","read_only","required","user_auth","gmail", "none","required","required",("gmail.readonly",),IdempotencyMode.NATURAL,RetryPolicy(2,1),True,description="Bounded Gmail search"),
        CapabilitySpec("email:read",Domain.EMAIL,"email.read",schema,schema,"medium","read_only","required","user_auth","gmail","none","required","required",("gmail.readonly",),IdempotencyMode.NATURAL,RetryPolicy(2,1),True,description="Bounded Gmail message read"),
        CapabilitySpec("email:thread",Domain.EMAIL,"email.thread",schema,schema,"medium","read_only","required","user_auth","gmail","none","required","required",("gmail.readonly",),IdempotencyMode.NATURAL,RetryPolicy(2,1),True,description="Bounded Gmail thread read"),
        CapabilitySpec("email:draft",Domain.EMAIL,"email.draft",schema,schema,"high","controlled_write","required","user_auth","gmail","explicit","required","required",("gmail.compose",),IdempotencyMode.REQUIRED,RetryPolicy(1),True,description="Create an unsent Gmail draft"),
        CapabilitySpec("email:send",Domain.EMAIL,"email.send",schema,schema,"critical","high_risk_write","required","user_auth","gmail","human_review","required","required",("gmail.send",),IdempotencyMode.REQUIRED,RetryPolicy(1),True,description="Send Gmail message only after human approval"),
    )
    for spec in specs: registry.register(spec)
    return registry
