"""Concrete Phase 6 M2 adapter over the existing bounded workspace connector.

This is the first real, read-only vertical slice. It wraps the already-existing
WorkspaceConnector and presents its bounded read operation through the Phase 6
real-backend contract.

Properties:
- root-bound path validation is delegated to WorkspaceConnector;
- credential/VCS paths remain blocked by WorkspaceConnector;
- returned content is the connector safe representation;
- observation re-reads the same file and independently recomputes the artifact digest;
- no write, shell, subprocess or network operation is exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..filesystem_workspace import WorkspaceConnector, WorkspaceError
from .models import (
    StepEffect,
    WorkflowArtifact,
    WorkflowValidationError,
    artifact_digest,
)
from .real_backend import (
    ExecutionEnvelope,
    ObservationEnvelope,
    ProviderOperationDescriptor,
    ProviderResult,
    RealBackendContractError,
    RealNetworkPolicy,
)

_PROVIDER_SECRET_LABEL = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|cookie|session|credential)\s*[:=]\s*"
)


def _provider_safe_payload(evidence: Any) -> dict[str, Any]:
    """Convert connector-safe evidence into a contract-safe provider payload.

    WorkspaceConnector already removes credential values. The workflow contract
    also rejects credential-looking field labels, so the adapter neutralizes
    those labels before provider evidence enters the real-backend boundary.
    """
    payload = dict(evidence.safe_dict())
    content = payload.get("content")
    if isinstance(content, str):
        payload["content"] = _PROVIDER_SECRET_LABEL.sub("redacted-field: ", content)
    return payload


@dataclass(frozen=True)
class _WorkspaceReadState:
    relative_path: str


class WorkspaceRealWorkflowAdapter:
    """Expose only filesystem:read through the M2 real-backend contract."""

    name = "workspace"

    def __init__(self, workspace: WorkspaceConnector) -> None:
        if not isinstance(workspace, WorkspaceConnector):
            raise RealBackendContractError(
                "WorkspaceRealWorkflowAdapter requires an existing WorkspaceConnector"
            )
        self._workspace = workspace
        self._states: dict[str, _WorkspaceReadState] = {}

    @property
    def workspace(self) -> WorkspaceConnector:
        return self._workspace

    def describe(self, capability_id: str, operation: str) -> ProviderOperationDescriptor:
        if capability_id != "filesystem:read" or operation != "read":
            raise RealBackendContractError(
                f"workspace adapter does not expose {capability_id}:{operation}"
            )
        return ProviderOperationDescriptor(
            capability_id="filesystem:read",
            operation="read",
            provider=self.name,
            network_policy=RealNetworkPolicy.NONE,
            required_scopes=("workspace.read",),
            effect=StepEffect.READ_ONLY,
            requires_approval=False,
            observable=True,
            idempotent=True,
        )

    @staticmethod
    def _path_from_step(step: Any) -> str:
        parameters = getattr(step, "parameters", {})
        if not isinstance(parameters, Mapping):
            raise WorkflowValidationError("filesystem:read parameters must be a mapping")
        unknown = set(parameters) - {"path"}
        if unknown:
            raise WorkflowValidationError(
                f"filesystem:read received unsupported parameters: {sorted(unknown)}"
            )
        path = parameters.get("path", ".")
        if not isinstance(path, str) or not path.strip():
            raise WorkflowValidationError(
                "filesystem:read requires a non-empty relative path"
            )
        return path.strip()

    @staticmethod
    def _artifact(evidence: Any) -> dict[str, Any]:
        return _provider_safe_payload(evidence)

    def execute(
        self,
        envelope: ExecutionEnvelope,
        step: Any,
        inbound: Mapping[str, WorkflowArtifact],
    ) -> ProviderResult:
        if inbound:
            raise WorkflowValidationError(
                "filesystem:read M2 vertical slice does not accept inbound artifacts"
            )

        relative_path = self._path_from_step(step)
        try:
            evidence = self._workspace.read(relative_path)
        except WorkspaceError as exc:
            return ProviderResult(
                accepted=False,
                artifacts={},
                evidence={
                    "operation": "filesystem:read",
                    "relative_path": relative_path,
                    "error": type(exc).__name__,
                },
                detail="workspace read was rejected by the bounded workspace connector",
            )

        payload = self._artifact(evidence)
        self._states[envelope.idempotency_key] = _WorkspaceReadState(
            relative_path=evidence.relative_path
        )

        return ProviderResult(
            accepted=True,
            artifacts={"result": payload},
            evidence={
                "operation": "filesystem:read",
                "relative_path": evidence.relative_path,
                "fingerprint": evidence.fingerprint,
                "redacted": evidence.redacted,
                "observation": "workspace file is re-read after execution",
            },
            detail="bounded workspace read accepted",
        )

    def observe(self, envelope: ExecutionEnvelope) -> ObservationEnvelope:
        state = self._states.get(envelope.idempotency_key)
        if state is None:
            raise RealBackendContractError(
                "workspace observation requested for an unknown execution envelope"
            )

        try:
            evidence = self._workspace.read(state.relative_path)
        except WorkspaceError as exc:
            return ObservationEnvelope(
                observed=False,
                evidence={
                    "operation": "filesystem:read",
                    "relative_path": state.relative_path,
                    "error": type(exc).__name__,
                },
                detail="workspace observation could not re-read the file",
            )

        payload = self._artifact(evidence)
        payload_digest = artifact_digest(payload)

        return ObservationEnvelope(
            observed=True,
            state_digest=payload_digest,
            artifact_digests={"result": payload_digest},
            evidence={
                "operation": "filesystem:read",
                "relative_path": evidence.relative_path,
                "fingerprint": evidence.fingerprint,
                "re_read": True,
            },
            detail="workspace file independently re-read after provider acceptance",
        )


__all__ = ["WorkspaceRealWorkflowAdapter"]