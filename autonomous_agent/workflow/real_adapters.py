"""Concrete Phase 6 adapters over the existing bounded workspace connector.

M2 provides the read-only slice; M3 provides one approval-gated, preconditioned
write slice. Both reuse the existing bounded WorkspaceConnector.

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


@dataclass(frozen=True)
class _WorkspaceWriteState:
    relative_path: str
    expected_content_digest: str


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
            recovery_path = envelope.recovery_context.get("path")
            if isinstance(recovery_path, str) and recovery_path.strip():
                state = _WorkspaceReadState(relative_path=recovery_path.strip())
            else:
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



class WorkspaceRealWorkflowWriteAdapter:
    """Expose one bounded filesystem:write operation with a precondition.

    The write slice is deliberately narrow: path + UTF-8 content only, an
    exact current-file fingerprint precondition, explicit human approval,
    deterministic idempotency, and an independent post-write re-read.
    """

    name = "workspace-write"

    def __init__(self, workspace: WorkspaceConnector) -> None:
        if not isinstance(workspace, WorkspaceConnector):
            raise RealBackendContractError(
                "WorkspaceRealWorkflowWriteAdapter requires an existing WorkspaceConnector"
            )
        self._workspace = workspace
        self._states: dict[str, _WorkspaceWriteState] = {}

    @property
    def workspace(self) -> WorkspaceConnector:
        return self._workspace

    def describe(self, capability_id: str, operation: str) -> ProviderOperationDescriptor:
        if capability_id != "filesystem:write" or operation != "write":
            raise RealBackendContractError(
                f"workspace write adapter does not expose {capability_id}:{operation}"
            )
        return ProviderOperationDescriptor(
            capability_id="filesystem:write",
            operation="write",
            provider=self.name,
            network_policy=RealNetworkPolicy.NONE,
            required_scopes=("workspace.write",),
            effect=StepEffect.MUTATING,
            requires_approval=True,
            observable=True,
            idempotent=True,
        )

    @staticmethod
    def _parameters_from_step(step: Any) -> tuple[str, str, str]:
        parameters = getattr(step, "parameters", {})
        if not isinstance(parameters, Mapping):
            raise WorkflowValidationError("filesystem:write parameters must be a mapping")
        unknown = set(parameters) - {"path", "content", "precondition"}
        if unknown:
            raise WorkflowValidationError(
                f"filesystem:write received unsupported parameters: {sorted(unknown)}"
            )

        path = parameters.get("path")
        content = parameters.get("content")
        precondition = parameters.get("precondition")
        if not isinstance(path, str) or not path.strip():
            raise WorkflowValidationError(
                "filesystem:write requires a non-empty relative path"
            )
        if not isinstance(content, str):
            raise WorkflowValidationError("filesystem:write content must be a string")
        if not isinstance(precondition, Mapping):
            raise WorkflowValidationError("filesystem:write requires a mapping precondition")
        if set(precondition) != {"fingerprint"}:
            raise WorkflowValidationError(
                "filesystem:write precondition must contain exactly fingerprint"
            )
        expected_fingerprint = precondition.get("fingerprint")
        if (
            not isinstance(expected_fingerprint, str)
            or len(expected_fingerprint) != 64
            or any(ch not in "0123456789abcdef" for ch in expected_fingerprint.lower())
        ):
            raise WorkflowValidationError(
                "filesystem:write precondition fingerprint must be a SHA-256 hex digest"
            )
        return path.strip(), content, expected_fingerprint.lower()

    @staticmethod
    def _precondition_digest(expected_fingerprint: str) -> str:
        return artifact_digest({"fingerprint": expected_fingerprint})

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
                "filesystem:write M3 vertical slice does not accept inbound artifacts"
            )

        relative_path, content, expected_fingerprint = self._parameters_from_step(step)
        expected_precondition = self._precondition_digest(expected_fingerprint)
        if envelope.precondition_digest != expected_precondition:
            raise RealBackendContractError(
                "execution precondition digest does not match the declared write precondition"
            )

        try:
            current = self._workspace.read(relative_path)
        except WorkspaceError as exc:
            return ProviderResult(
                accepted=False,
                artifacts={},
                evidence={
                    "operation": "filesystem:write",
                    "relative_path": relative_path,
                    "precondition_met": False,
                    "error": type(exc).__name__,
                },
                detail="write precondition could not be read from the bounded workspace",
            )

        if current.fingerprint.lower() != expected_fingerprint:
            return ProviderResult(
                accepted=False,
                artifacts={},
                evidence={
                    "operation": "filesystem:write",
                    "relative_path": relative_path,
                    "precondition_met": False,
                    "expected_fingerprint": expected_fingerprint,
                    "actual_fingerprint": current.fingerprint.lower(),
                },
                detail="write precondition fingerprint is stale",
            )

        try:
            evidence = self._workspace.write(relative_path, content)
        except WorkspaceError as exc:
            return ProviderResult(
                accepted=False,
                artifacts={},
                evidence={
                    "operation": "filesystem:write",
                    "relative_path": relative_path,
                    "precondition_met": True,
                    "error": type(exc).__name__,
                },
                detail="workspace write was rejected by the bounded workspace connector",
            )

        payload = self._artifact(evidence)
        self._states[envelope.idempotency_key] = _WorkspaceWriteState(
            relative_path=evidence.relative_path,
            expected_content_digest=artifact_digest(
                {"path": evidence.relative_path, "content": content}
            ),
        )
        return ProviderResult(
            accepted=True,
            artifacts={"result": payload},
            evidence={
                "operation": "filesystem:write",
                "relative_path": evidence.relative_path,
                "precondition_met": True,
                "postcondition_expected": True,
                "fingerprint": evidence.fingerprint,
            },
            detail="bounded workspace write accepted; independent post-write observation required",
        )

    def observe(self, envelope: ExecutionEnvelope) -> ObservationEnvelope:
        state = self._states.get(envelope.idempotency_key)
        if state is None:
            recovery_path = envelope.recovery_context.get("path")
            recovery_content_digest = envelope.recovery_context.get("content_digest")
            if (
                isinstance(recovery_path, str)
                and recovery_path.strip()
                and isinstance(recovery_content_digest, str)
                and len(recovery_content_digest) == 64
            ):
                state = _WorkspaceWriteState(
                    relative_path=recovery_path.strip(),
                    expected_content_digest=recovery_content_digest,
                )
            else:
                raise RealBackendContractError(
                    "workspace write observation requested for an unknown execution envelope"
                )

        try:
            evidence = self._workspace.read(state.relative_path)
        except WorkspaceError as exc:
            return ObservationEnvelope(
                observed=False,
                evidence={
                    "operation": "filesystem:write",
                    "relative_path": state.relative_path,
                    "error": type(exc).__name__,
                },
                detail="workspace write postcondition could not be observed",
            )

        expected_fingerprint = artifact_digest(
            {"path": evidence.relative_path, "content": evidence.content}
        )
        if expected_fingerprint != state.expected_content_digest:
            return ObservationEnvelope(
                observed=False,
                evidence={
                    "operation": "filesystem:write",
                    "relative_path": evidence.relative_path,
                    "postcondition_match": False,
                    "observed_fingerprint": evidence.fingerprint,
                },
                detail="independent postcondition observation does not match the requested content",
            )

        payload = self._artifact(evidence)
        # The execution artifact represents the resulting write state. Keep the
        # observation payload semantically identical while recording the actual
        # read-back fact separately in ObservationEnvelope.evidence.
        payload["operation"] = "write"
        payload_digest = artifact_digest(payload)
        return ObservationEnvelope(
            observed=True,
            state_digest=payload_digest,
            artifact_digests={"result": payload_digest},
            evidence={
                "operation": "filesystem:write",
                "relative_path": evidence.relative_path,
                "postcondition_match": True,
                "re_read": True,
            },
            detail="workspace write independently re-read and postcondition matched",
        )


__all__ = [
    "WorkspaceRealWorkflowAdapter",
    "WorkspaceRealWorkflowWriteAdapter",
]