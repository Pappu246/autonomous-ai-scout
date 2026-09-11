from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .capability_policy import CapabilityDecision
from .tool_registry import ToolRegistry


class Domain(str, Enum):
    GITHUB = "github"
    WEB = "web"
    FILES = "files"
    EMAIL = "email"
    AI = "ai"
    CALENDAR = "calendar"
    API = "api"
    BROWSER = "browser"


class IdempotencyMode(str, Enum):
    NONE = "none"
    REQUIRED = "required"
    NATURAL = "natural"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: int = 0
    def __post_init__(self):
        if not 1 <= self.max_attempts <= 3 or not 0 <= self.backoff_seconds <= 300:
            raise ValueError("retry policy is outside bounded safe limits")


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    domain: Domain
    operation: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    risk: str
    read_write: str
    network: str
    authentication: str
    credential_reference: str | None
    approval: str
    sandbox: str
    audit: str
    scope: tuple[str, ...]
    idempotency: IdempotencyMode
    retry_policy: RetryPolicy
    enabled: bool
    version: int = 1
    description: str = ""
    timeout_seconds: int = 10

    def __post_init__(self):
        if not self.capability_id or ":" not in self.capability_id: raise ValueError("capability id must be namespaced")
        if not self.operation or not self.scope: raise ValueError("operation and scope are required")
        if self.version < 1 or not 1 <= self.timeout_seconds <= 120: raise ValueError("invalid version or timeout")
        if self.read_write != "read_only" and self.approval == "none": raise ValueError("write capability requires approval")
        if self.authentication != "none" and self.credential_reference is None: raise ValueError("authenticated capability requires a credential reference")
        if self.credential_reference is not None and any(secret in self.credential_reference.lower() for secret in ("token", "password", "secret", "key=")): raise ValueError("credential reference must not contain credential material")

    @property
    def fingerprint(self):
        payload = {"id": self.capability_id, "domain": self.domain.value, "operation": self.operation, "description": self.description, "input": self.input_schema, "output": self.output_schema, "risk": self.risk, "read_write": self.read_write, "network": self.network, "authentication": self.authentication, "credential_reference": self.credential_reference, "approval": self.approval, "sandbox": self.sandbox, "audit": self.audit, "scope": self.scope, "idempotency": self.idempotency.value, "retry": {"max_attempts": self.retry_policy.max_attempts, "backoff_seconds": self.retry_policy.backoff_seconds}, "timeout_seconds": self.timeout_seconds, "enabled": self.enabled, "version": self.version}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class CapabilityRegistry:
    """Metadata only; ToolRegistry remains the sole authorization authority."""
    def __init__(self, tool_registry: ToolRegistry): self._tools, self._items = tool_registry, {}
    def register(self, spec):
        if spec.capability_id in self._items: raise ValueError("duplicate capability id")
        if spec.enabled and not self._tools.get(spec.operation): raise ValueError("capability operation is not registered in ToolRegistry")
        self._items[spec.capability_id] = spec
    def get(self, capability_id): return self._items.get(capability_id)
    def list(self, *, domain=None): return tuple(sorted((x for x in self._items.values() if domain is None or x.domain is domain), key=lambda x: x.capability_id))
    def authorize(self, capability_id, granted=()):
        spec = self._items.get(capability_id)
        if spec is None or not spec.enabled: return CapabilityDecision(False, "capability is unknown or disabled", capability_id)
        return self._tools.authorize(spec.operation, granted)


def github_capabilities(tool_registry):
    registry = CapabilityRegistry(tool_registry); schema = {"type": "object", "properties": {}, "additionalProperties": True}
    registry.register(CapabilitySpec("github:inspect", Domain.GITHUB, "github.inspect", schema, schema, "low", "read_only", "required", "user_auth", "github", "none", "none", "required", ("repository:read",), IdempotencyMode.NATURAL, RetryPolicy(2, 1), True, description="Read repository state"))
    registry.register(CapabilitySpec("github:status", Domain.GITHUB, "github.inspect", schema, schema, "low", "read_only", "required", "user_auth", "github", "none", "none", "required", ("repository:read", "checks:read"), IdempotencyMode.NATURAL, RetryPolicy(2, 1), True, description="Read repository checks"))
    registry.register(CapabilitySpec("github:change", Domain.GITHUB, "github.change", schema, schema, "high", "controlled_write", "required", "user_auth", "github", "explicit", "required", "required", ("repository:change:approved",), IdempotencyMode.REQUIRED, RetryPolicy(), True, description="Prepare approved source change"))
    return registry


def web_capabilities(tool_registry):
    registry = CapabilityRegistry(tool_registry); schema = {"type": "object", "additionalProperties": True}
    for cid, op, desc, network, timeout, retries in (("web:search", "web.search", "Bounded public web search", "required", 10, 2), ("web:read", "web.read", "Bounded public page read", "required", 10, 2), ("web:extract", "web.extract", "Deterministic extraction", "none", 5, 1), ("web:compare", "web.compare", "Evidence-backed comparison", "none", 5, 1)):
        registry.register(CapabilitySpec(cid, Domain.WEB, op, schema, schema, "medium" if network == "required" else "low", "read_only", network, "none", None, "none", "required", "required", ("public:read",), IdempotencyMode.NATURAL, RetryPolicy(retries, 1 if network == "required" else 0), True, description=desc, timeout_seconds=timeout))
    return registry
