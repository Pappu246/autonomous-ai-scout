"""Shared builders for the digital agent architecture tests."""

from __future__ import annotations

from typing import Any, Mapping

from autonomous_agent.digital.authorization import CapabilityAuthorizationBroker
from autonomous_agent.digital.builtins import BUILTIN_DECLARATIONS, CapabilityDeclaration
from autonomous_agent.digital.catalog import CapabilityCatalog
from autonomous_agent.digital.contract import CapabilityExecution, CapabilityObservation
from autonomous_agent.digital.planner import CapabilityPlanner
from autonomous_agent.digital.provider import RegisteredToolCapability
from autonomous_agent.digital.runtime import DigitalAgentRuntime
from autonomous_agent.sandbox import SandboxResult
from autonomous_agent.tool_registry import REGISTRY, ToolRegistry


NOW = "2026-01-01T00:00:00+00:00"


def make_result(
    *,
    success: bool = True,
    output: str = '{"entries": ["a.txt"], "fingerprint": "abc"}',
    verification_status: str = "verified",
    operation: str = "filesystem_workspace",
    exit_status: int | None = 0,
) -> SandboxResult:
    return SandboxResult(
        operation,
        success,
        exit_status if success else 1,
        output,
        False,
        (),
        verification_status if success else "failed",
        NOW,
        NOW,
        True,
    )


class RecordingExecutor:
    """Records calls and returns canned sandbox results.

    Raises nothing by default: it stands in for the real sandbox so tests can
    assert exactly which capabilities the lifecycle decided to execute.
    """

    def __init__(self, results: Mapping[str, Any] | None = None) -> None:
        self.results = dict(results or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_count(self, tool_name: str) -> int:
        return sum(1 for name, _ in self.calls if name == tool_name)

    def run(self, tool_name: str, arguments: Mapping[str, Any]) -> SandboxResult:
        self.calls.append((tool_name, dict(arguments)))
        configured = self.results.get(tool_name)
        if isinstance(configured, Exception):
            raise configured
        if configured is not None:
            return configured
        return make_result()


class FlakyExecutor:
    """Fails the first ``failures`` calls for a tool, then succeeds."""

    def __init__(self, failures: Mapping[str, int] | None = None) -> None:
        self.failures = dict(failures or {})
        self.calls: list[str] = []

    def call_count(self, tool_name: str) -> int:
        return sum(1 for name in self.calls if name == tool_name)

    def run(self, tool_name: str, arguments: Mapping[str, Any]) -> SandboxResult:
        self.calls.append(tool_name)
        remaining = self.failures.get(tool_name, 0)
        if remaining > 0:
            self.failures[tool_name] = remaining - 1
            return make_result(success=False, output="transient failure")
        return make_result()


class NoopExecutor:
    """Always reports success while producing no observable evidence."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, tool_name: str, arguments: Mapping[str, Any]) -> SandboxResult:
        self.calls += 1
        return make_result(success=True, output="{}", verification_status="verified")


class AlwaysObservedExecutor(RecordingExecutor):
    pass


class StubObserver:
    """Observer that can be forced to confirm or reject a post-condition."""

    def __init__(self, observed: bool = True, evidence: Mapping[str, Any] | None = None) -> None:
        self.observed = observed
        self.evidence = dict(evidence if evidence is not None else {"confirmed": True})
        self.calls = 0

    def observe(
        self, request: Any, execution: CapabilityExecution
    ) -> CapabilityObservation:
        self.calls += 1
        return CapabilityObservation(
            request.capability_id,
            self.observed,
            self.evidence if self.observed else {},
            "stub observation",
        )


def build_capability(
    capability_id: str,
    declaration: CapabilityDeclaration,
    executor: Any,
    *,
    tool_registry: ToolRegistry = REGISTRY,
    observer: Any = None,
    audit_sink: Any = None,
) -> RegisteredToolCapability:
    return RegisteredToolCapability.from_tool(
        declaration.tool_name,
        domain=declaration.domain,
        capability_id=declaration.capability_id,
        signals=declaration.signals,
        stage=declaration.stage,
        retry_policy=declaration.retry_policy,
        credential_handling=declaration.credential_handling,
        executor=executor,
        tool_registry=tool_registry,
        observer=observer,
        audit_sink=audit_sink,
    )


DECLARATIONS_BY_ID = {item.capability_id: item for item in BUILTIN_DECLARATIONS}


def build_catalog(
    capability_ids: tuple[str, ...],
    executor: Any,
    *,
    tool_registry: ToolRegistry = REGISTRY,
    observers: Mapping[str, Any] | None = None,
) -> CapabilityCatalog:
    observers = observers or {}
    return CapabilityCatalog(
        tuple(
            build_capability(
                capability_id,
                DECLARATIONS_BY_ID[capability_id],
                executor,
                tool_registry=tool_registry,
                observer=observers.get(capability_id),
            )
            for capability_id in capability_ids
        )
    )


def build_runtime(
    capability_ids: tuple[str, ...],
    executor: Any,
    *,
    tool_registry: ToolRegistry = REGISTRY,
    observers: Mapping[str, Any] | None = None,
) -> tuple[DigitalAgentRuntime, CapabilityCatalog]:
    catalog = build_catalog(
        capability_ids, executor, tool_registry=tool_registry, observers=observers
    )
    broker = CapabilityAuthorizationBroker(catalog, tool_registry=tool_registry)
    planner = CapabilityPlanner(catalog, authorization=broker)
    runtime = DigitalAgentRuntime(
        catalog, planner=planner, authorization=broker, tool_registry=tool_registry
    )
    return runtime, catalog
