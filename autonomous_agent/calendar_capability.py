from __future__ import annotations
from .capability_policy import Capability
from .universal_capability import CapabilityRegistry,CapabilitySpec,Domain,IdempotencyMode,RetryPolicy
def calendar_capabilities(tool_registry):
    registry=CapabilityRegistry(tool_registry);schema={"type":"object","additionalProperties":True}
    specs=(
      CapabilitySpec("calendar:read",Domain.CALENDAR,"calendar.read",schema,schema,"low","read_only","required","user_auth","calendar","none","required","required",("calendar.readonly",),IdempotencyMode.NATURAL,RetryPolicy(2,1),True,description="Read a bounded calendar event"),
      CapabilitySpec("calendar:list",Domain.CALENDAR,"calendar.list",schema,schema,"low","read_only","required","user_auth","calendar","none","required","required",("calendar.readonly",),IdempotencyMode.NATURAL,RetryPolicy(2,1),True,description="List bounded calendar events"),
      CapabilitySpec("calendar:free_time",Domain.CALENDAR,"calendar.find_free_time",schema,schema,"low","read_only","required","user_auth","calendar","none","required","required",("calendar.readonly",),IdempotencyMode.NATURAL,RetryPolicy(2,1),True,description="Find available time"),
      CapabilitySpec("calendar:create",Domain.CALENDAR,"calendar.event.create",schema,schema,"high","controlled_write","required","user_auth","calendar","human_review","required","required",("calendar.events",),IdempotencyMode.REQUIRED,RetryPolicy(1),True,description="Create an approved calendar event"),
      CapabilitySpec("calendar:update",Domain.CALENDAR,"calendar.event.update",schema,schema,"high","controlled_write","required","user_auth","calendar","human_review","required","required",("calendar.events",),IdempotencyMode.REQUIRED,RetryPolicy(1),True,description="Update an approved calendar event"),
      CapabilitySpec("calendar:cancel",Domain.CALENDAR,"calendar.event.cancel",schema,schema,"critical","high_risk_write","required","user_auth","calendar","human_review","required","required",("calendar.events",),IdempotencyMode.REQUIRED,RetryPolicy(1),True,description="Cancel an approved calendar event"))
    for spec in specs:registry.register(spec)
    return registry
