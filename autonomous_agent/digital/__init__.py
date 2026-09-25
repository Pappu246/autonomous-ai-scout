"""Universal bounded digital agent -- Phase 1 foundation.

This package turns the repository-focused scout into a general-purpose digital
agent: the user states a goal in natural language, the agent works out which
digital capabilities the goal needs, and executes through registered adapters
under the existing authorization, sandbox and audit boundaries.

    from autonomous_agent.digital import build_agent

    agent = build_agent(root=".", connectors={"workspace": connector})
    result = agent.run(
        "list the files in the docs folder",
        root=".",
        audit_path="state/digital_audit.jsonl",
        execution_id="run-1",
        requests={"filesystem:list": {"path": "docs"}},
    )

Design rules that hold across the package:

* GitHub is one capability domain among many -- never the definition of the
  product.
* A capability cannot authorize itself; the process tool registry is the only
  authority and is re-consulted immediately before every execution.
* Unknown capabilities and unregistered domains fail closed.
* Success is only reported as ``VERIFIED`` when observable evidence exists, so
  no-op and failed operations can never be presented as verified.
* Reserved domains (computer control, application adapters, document
  processing) are declared but deliberately unimplemented; they block rather
  than pretend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..prompt_injection_guard import TrustLevel
from ..tool_registry import REGISTRY, ToolRegistry
from .authorization import CapabilityAuthorizationBroker
from .builtins import (
    BUILTIN_DECLARATIONS,
    DEFAULT_CAPABILITIES,
    CapabilityDeclaration,
    build_capabilities,
)
from .catalog import CapabilityCatalog
from .contract import (
    CapabilityAuditRecord,
    CapabilityAvailability,
    CapabilityDescriptor,
    CapabilityError,
    CapabilityExecution,
    CapabilityObservation,
    CapabilityOutcome,
    CapabilityRequest,
    CapabilityVerification,
    DigitalCapability,
    InputValidation,
    RetryPolicy,
)
from .domains import (
    DOMAIN_DESCRIPTORS,
    CapabilityDomain,
    DomainDescriptor,
    DomainPhase,
    active_domains,
    reserved_domains,
)
from .intent import DigitalGoal, IntentProfile, understand_goal
from .planner import CapabilityPlan, CapabilityPlanner, PlannedStep
from .provider import (
    RegisteredToolCapability,
    SandboxCapabilityExecutor,
    TOOL_SANDBOX_BINDINGS,
    redact_secret_material,
)
from .runtime import DigitalAgentRuntime, DigitalResult, DigitalResultState, StepRecord


class UniversalDigitalAgent:
    """One facade over the canonical lifecycle."""

    def __init__(self, runtime: DigitalAgentRuntime) -> None:
        self.runtime = runtime

    @property
    def catalog(self) -> CapabilityCatalog:
        return self.runtime.catalog

    def capabilities(self, **kwargs: Any):
        """Discover registered capabilities."""
        return self.catalog.discover(**kwargs)

    def domain_status(self):
        """Honest availability of every declared domain, reserved included."""
        return self.catalog.domain_status()

    def documentation(self, version: int = 1):
        """Machine-readable capability documentation for humans and adapters."""
        return self.catalog.documentation(version)

    def understand(self, goal: DigitalGoal | str) -> IntentProfile:
        return self.runtime.understand(goal)

    def plan(self, goal: DigitalGoal | str, **kwargs: Any) -> CapabilityPlan:
        return self.runtime.plan(goal, **kwargs)

    def run(self, goal: DigitalGoal | str, **kwargs: Any) -> DigitalResult:
        return self.runtime.run(goal, **kwargs)


def build_agent(
    *,
    root: Path | str,
    connectors: Mapping[str, Any] | None = None,
    tool_registry: ToolRegistry = REGISTRY,
    declarations: Any = BUILTIN_DECLARATIONS,
    timeout_seconds: int = 30,
) -> UniversalDigitalAgent:
    """Assemble a digital agent from real, registered capabilities only."""
    catalog = CapabilityCatalog(
        build_capabilities(
            root=root,
            connectors=connectors,
            tool_registry=tool_registry,
            declarations=declarations,
            timeout_seconds=timeout_seconds,
        )
    )
    broker = CapabilityAuthorizationBroker(catalog, tool_registry=tool_registry)
    planner = CapabilityPlanner(catalog, authorization=broker)
    return UniversalDigitalAgent(
        DigitalAgentRuntime(
            catalog, planner=planner, authorization=broker, tool_registry=tool_registry
        )
    )


__all__ = [
    "BUILTIN_DECLARATIONS",
    "DEFAULT_CAPABILITIES",
    "DOMAIN_DESCRIPTORS",
    "TOOL_SANDBOX_BINDINGS",
    "CapabilityAuditRecord",
    "CapabilityAuthorizationBroker",
    "CapabilityAvailability",
    "CapabilityCatalog",
    "CapabilityDeclaration",
    "CapabilityDescriptor",
    "CapabilityDomain",
    "CapabilityError",
    "CapabilityExecution",
    "CapabilityObservation",
    "CapabilityOutcome",
    "CapabilityPlan",
    "CapabilityPlanner",
    "CapabilityRequest",
    "CapabilityVerification",
    "DigitalAgentRuntime",
    "DigitalCapability",
    "DigitalGoal",
    "DigitalResult",
    "DigitalResultState",
    "DomainDescriptor",
    "DomainPhase",
    "InputValidation",
    "IntentProfile",
    "PlannedStep",
    "RegisteredToolCapability",
    "RetryPolicy",
    "SandboxCapabilityExecutor",
    "StepRecord",
    "TrustLevel",
    "UniversalDigitalAgent",
    "active_domains",
    "build_agent",
    "build_capabilities",
    "redact_secret_material",
    "reserved_domains",
    "understand_goal",
]
