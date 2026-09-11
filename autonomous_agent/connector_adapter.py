from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .capability_policy import Capability, CapabilityDecision
from .connector_registry import ConnectorRegistry, ConnectorRegistryError
from .tool_registry import ToolRegistry, REGISTRY


@dataclass(frozen=True)
class ConnectorRequest:
    """A validated, authorization-bound request; it contains no credential material."""

    connector_id: str
    tool_name: str
    input_data: object
    credential_reference: str | None = None


@dataclass(frozen=True)
class ConnectorPreparation:
    request: ConnectorRequest
    authorization: CapabilityDecision


class ExistingSafeExecutor(Protocol):
    """Protocol for the existing executor; adapters must not implement a second executor."""

    def execute(self, *args: object, **kwargs: object) -> object: ...


class ConnectorAdapter:
    """Safe adapter boundary: validate -> resolve -> authorize, then hand off to the existing executor."""

    def __init__(self, registry: ConnectorRegistry, *, tool_registry: ToolRegistry = REGISTRY):
        self._registry = registry
        self._tool_registry = tool_registry

    def prepare(
        self,
        connector_id: str,
        tool_name: str,
        input_data: object,
        granted: Iterable[Capability | str] = (),
        *,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
        credential_reference: str | None = None,
    ) -> ConnectorPreparation:
        spec = self._registry.get(connector_id)
        if spec is None:
            raise ConnectorRegistryError("connector is not registered")
        if not spec.enabled:
            raise ConnectorRegistryError("connector is disabled")
        tool = self._registry.resolve_tool(connector_id, tool_name)
        if tool is None:
            raise ConnectorRegistryError("tool is not exposed by this connector or is unavailable")
        decision = self._registry.authorize(
            connector_id,
            granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
            registry=self._tool_registry,
        )
        if not decision.allowed:
            return ConnectorPreparation(
                ConnectorRequest(connector_id, tool_name, input_data, credential_reference),
                decision,
            )
        return ConnectorPreparation(
            ConnectorRequest(connector_id, tool_name, input_data, credential_reference),
            decision,
        )

    def execute_through_existing_boundary(
        self,
        preparation: ConnectorPreparation,
        executor: ExistingSafeExecutor,
        *args: object,
        **kwargs: object,
    ) -> object:
        """Only delegates to the supplied existing executor; no connector execution is implemented here."""
        if not preparation.authorization.allowed:
            raise ConnectorRegistryError("connector request is not authorized")
        return executor.execute(*args, **kwargs)
