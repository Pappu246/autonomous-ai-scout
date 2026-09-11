from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Iterable, Protocol

from .capability_policy import Capability, CapabilityDecision
from .connector_registry import ConnectorRegistry, ConnectorRegistryError, CredentialHandling
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


def _validate_input_schema(value: object, schema: Mapping[str, object]) -> None:
    expected = schema.get("type")
    valid = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, (list, tuple)),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }.get(expected, False)
    if not valid:
        raise ConnectorRegistryError("connector request does not match its declared input schema")


def _validate_credential_reference(reference: str | None, handling: CredentialHandling) -> None:
    if reference is None:
        return
    if handling is CredentialHandling.NONE:
        raise ConnectorRegistryError("connector does not accept credential references")
    if not isinstance(reference, str) or not reference.startswith("credref:") or len(reference) > 256:
        raise ConnectorRegistryError("credential input must be a bounded credref identifier")
    if any(token in reference.lower() for token in ("secret=", "password=", "token=", "api_key=", "key=")):
        raise ConnectorRegistryError("raw credential material is not accepted")


class ConnectorAdapter:
    """Safe adapter boundary: validate -> resolve -> authorize -> existing executor."""

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
        _validate_input_schema(input_data, spec.input_schema)
        _validate_credential_reference(credential_reference, spec.credential_handling)
        decision = self._registry.authorize(
            connector_id,
            granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
            registry=self._tool_registry,
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
        """Only delegates to the supplied existing executor; no connector executor exists here."""
        if not preparation.authorization.allowed:
            raise ConnectorRegistryError("connector request is not authorized")
        return executor.execute(*args, **kwargs)
