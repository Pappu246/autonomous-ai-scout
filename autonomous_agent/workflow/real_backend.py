"""M1 contract for bounded real workflow execution.

This module deliberately contains **no concrete network/provider client**.  A
real adapter must be injected explicitly.  The backend only validates and
binds execution metadata, delegates to that adapter, records a non-verified
provider result, and exposes an observation surface for the existing Phase 5
connector to verify independently.

The design rule is:

    provider acceptance != verification

The provider is never allowed to manufacture authority, approval, network
policy, or verification state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from ..prompt_injection_guard import TrustLevel
from .backend import BaseWorkflowBackend, DELIVERY_OPERATIONS
from .models import (
    MAX_ARTIFACT_KEYS,
    MAX_CAPABILITY_ID_LENGTH,
    MAX_DETAIL_LENGTH,
    MAX_OPERATION_LENGTH,
    BackendUnavailableError,
    HandoffKind,
    StepEffect,
    StepExecution,
    WorkflowArtifact,
    WorkflowObservation,
    WorkflowPipeline,
    WorkflowReplayError,
    WorkflowSecurityError,
    WorkflowStep,
    WorkflowValidationError,
    artifact_digest,
)
from .policy import (
    FORBIDDEN_STEP_OPERATIONS,
    assert_secret_free,
    domain_trust,
    validate_operation,
    validate_pipeline,
)


class RealBackendContractError(WorkflowValidationError):
    """Raised when an injected real adapter violates the M1 contract."""


class RealNetworkPolicy(str, Enum):
    """Network authority explicitly declared by a real provider adapter."""

    NONE = "none"
    BOUNDED_PROVIDER = "bounded_provider"


def _bounded_text(value: Any, label: str, *, limit: int) -> str:
    if not isinstance(value, str):
        raise RealBackendContractError(f"{label} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise RealBackendContractError(f"{label} must not be empty")
    if len(cleaned) > limit:
        raise RealBackendContractError(
            f"{label} exceeds max length {limit}: {len(cleaned)}"
        )
    if any(ord(char) < 32 for char in cleaned):
        raise RealBackendContractError(f"{label} contains control characters")
    return cleaned


def _scope_tuple(scopes: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(scopes, (list, tuple)):
        raise RealBackendContractError("required_scopes must be a list or tuple")
    if len(scopes) > 32:
        raise RealBackendContractError("required_scopes exceeds 32 entries")
    cleaned = []
    for scope in scopes:
        item = _bounded_text(scope, "scope", limit=128).lower()
        if item not in cleaned:
            cleaned.append(item)
    return tuple(cleaned)


@dataclass(frozen=True)
class ProviderOperationDescriptor:
    """Provider operation metadata; it is policy data, not authorization."""

    capability_id: str
    operation: str
    provider: str
    network_policy: RealNetworkPolicy = RealNetworkPolicy.NONE
    required_scopes: tuple[str, ...] = ()
    effect: StepEffect = StepEffect.READ_ONLY
    requires_approval: bool = False
    observable: bool = True
    idempotent: bool = True

    def __post_init__(self) -> None:
        capability = _bounded_text(
            self.capability_id, "capability_id", limit=MAX_CAPABILITY_ID_LENGTH
        )
        operation = validate_operation(self.operation)
        provider = _bounded_text(self.provider, "provider", limit=128)

        if operation in DELIVERY_OPERATIONS or operation.rsplit(".", 1)[-1] in DELIVERY_OPERATIONS:
            raise WorkflowSecurityError(
                "real workflow adapters may not expose autonomous delivery operations"
            )
        if any(part in operation.split(".") for part in FORBIDDEN_STEP_OPERATIONS):
            raise WorkflowSecurityError(
                f"real workflow adapter operation is forbidden: {operation}"
            )
        if not isinstance(self.network_policy, RealNetworkPolicy):
            raise RealBackendContractError("network_policy must be a RealNetworkPolicy")
        scopes = _scope_tuple(self.required_scopes)
        object.__setattr__(self, "capability_id", capability)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "required_scopes", scopes)

        if self.effect is StepEffect.MUTATING and not self.requires_approval:
            raise WorkflowSecurityError(
                "a mutating real operation must declare requires_approval=True"
            )
        if not isinstance(self.observable, bool) or not self.observable:
            raise RealBackendContractError(
                "a real workflow operation must declare an observation path"
            )
        if not isinstance(self.idempotent, bool) or not self.idempotent:
            raise RealBackendContractError(
                "M1 real operations must be idempotent or safely deduplicated"
            )

    def safe_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "operation": self.operation,
            "provider": self.provider,
            "network_policy": self.network_policy.value,
            "required_scopes": list(self.required_scopes),
            "effect": self.effect.value,
            "requires_approval": self.requires_approval,
            "observable": self.observable,
            "idempotent": self.idempotent,
        }


@dataclass(frozen=True)
class ExecutionEnvelope:
    """Secret-free identity of one provider execution request."""

    workflow_id: str
    step_id: str
    capability_id: str
    operation: str
    provider: str
    idempotency_key: str
    input_digest: str
    authorization_digest: str
    precondition_digest: str = ""
    network_policy: RealNetworkPolicy = RealNetworkPolicy.NONE

    def safe_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "step_id": self.step_id,
            "capability_id": self.capability_id,
            "operation": self.operation,
            "provider": self.provider,
            "idempotency_key": self.idempotency_key,
            "input_digest": self.input_digest,
            "authorization_digest": self.authorization_digest,
            "precondition_digest": self.precondition_digest,
            "network_policy": self.network_policy.value,
        }

    @classmethod
    def build(
        cls,
        *,
        workflow_id: str,
        step: WorkflowStep,
        provider: str,
        network_policy: RealNetworkPolicy,
        required_scopes: Sequence[str],
        inbound: Mapping[str, WorkflowArtifact] | None = None,
        approved: bool = False,
        precondition_digest: str = "",
    ) -> "ExecutionEnvelope":
        inbound = inbound or {}
        input_digest = artifact_digest(
            {
                "workflow_id": workflow_id,
                "step": step.safe_dict(),
                "inbound": {
                    key: value.sha256 or artifact_digest(value.payload)
                    for key, value in sorted(inbound.items())
                },
            }
        )
        authorization_digest = artifact_digest(
            {
                "capability_id": step.capability_id,
                "required_scopes": list(required_scopes),
                "requires_approval": step.requires_approval,
                "approved": bool(approved),
            }
        )
        idempotency_key = artifact_digest(
            {
                "workflow_id": workflow_id,
                "step_id": step.step_id,
                "capability_id": step.capability_id,
                "operation": step.operation,
                "input_digest": input_digest,
                "precondition_digest": precondition_digest,
            }
        )
        return cls(
            workflow_id=workflow_id,
            step_id=step.step_id,
            capability_id=step.capability_id,
            operation=step.operation,
            provider=_bounded_text(provider, "provider", limit=128),
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            authorization_digest=authorization_digest,
            precondition_digest=str(precondition_digest or ""),
            network_policy=network_policy,
        )


@dataclass(frozen=True)
class ProviderResult:
    """Provider response accepted by the adapter contract.

    The response is *not* a verification result.  Artifacts and evidence are
    checked again by the existing Phase 5 connector/observer.
    """

    accepted: bool
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    provider_request_id: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise RealBackendContractError("ProviderResult.accepted must be bool")
        if not isinstance(self.artifacts, Mapping):
            raise RealBackendContractError("ProviderResult.artifacts must be a mapping")
        if not isinstance(self.evidence, Mapping):
            raise RealBackendContractError("ProviderResult.evidence must be a mapping")
        if len(self.artifacts) > MAX_ARTIFACT_KEYS:
            raise RealBackendContractError(
                f"provider returned too many artifacts: {len(self.artifacts)}"
            )
        # Provider output becomes workflow state.  Fail closed rather than
        # persist credential material in artifacts or evidence.
        assert_secret_free(self.artifacts, "real provider artifacts")
        assert_secret_free(self.evidence, "real provider evidence")
        if self.provider_request_id:
            _bounded_text(
                self.provider_request_id, "provider_request_id", limit=256
            )
        if self.detail:
            _bounded_text(self.detail, "detail", limit=MAX_DETAIL_LENGTH)

    def safe_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "artifacts": dict(self.artifacts),
            "evidence": dict(self.evidence),
            "provider_request_id": self.provider_request_id[:256],
            "detail": self.detail[:MAX_DETAIL_LENGTH],
        }


@dataclass(frozen=True)
class ObservationEnvelope:
    """Independent provider observation returned after execution."""

    observed: bool
    state_digest: str = ""
    artifact_digests: Mapping[str, str] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.observed, bool):
            raise RealBackendContractError("ObservationEnvelope.observed must be bool")
        if not isinstance(self.artifact_digests, Mapping):
            raise RealBackendContractError(
                "ObservationEnvelope.artifact_digests must be a mapping"
            )
        if len(self.artifact_digests) > MAX_ARTIFACT_KEYS:
            raise RealBackendContractError(
                "observation contains too many artifacts"
            )
        for key, digest in self.artifact_digests.items():
            if not isinstance(key, str) or not key:
                raise RealBackendContractError("observation artifact keys must be non-empty strings")
            if not isinstance(digest, str) or not digest:
                raise RealBackendContractError("observation artifact digests must be non-empty strings")
        assert_secret_free(self.evidence, "real provider observation evidence")
        if self.state_digest and not isinstance(self.state_digest, str):
            raise RealBackendContractError("state_digest must be a string")
        if self.detail:
            _bounded_text(self.detail, "detail", limit=MAX_DETAIL_LENGTH)

    def safe_dict(self) -> dict[str, Any]:
        return {
            "observed": self.observed,
            "state_digest": self.state_digest,
            "artifact_digests": dict(sorted(self.artifact_digests.items())),
            "evidence": dict(self.evidence),
            "detail": self.detail[:MAX_DETAIL_LENGTH],
        }


class RealWorkflowAdapter(Protocol):
    """Explicit provider adapter required for any real execution."""

    name: str

    def describe(self, capability_id: str, operation: str) -> ProviderOperationDescriptor:
        """Return the exact operation descriptor or raise if unsupported."""

    def execute(
        self,
        envelope: ExecutionEnvelope,
        step: WorkflowStep,
        inbound: Mapping[str, WorkflowArtifact],
    ) -> ProviderResult:
        """Execute one declared operation under the supplied envelope."""

    def observe(
        self,
        envelope: ExecutionEnvelope,
    ) -> ObservationEnvelope:
        """Re-read provider state after execution without changing it."""


class ControlledRealWorkflowBackend(BaseWorkflowBackend):
    """M1 real backend shell; concrete provider access is injected explicitly.

    With no adapter, every operation fails closed.  With an injected adapter,
    this class only performs contract checks and delegates.  It never opens
    network sockets, shells, subprocesses, browsers or credential stores itself.
    """

    name = "controlled-real"

    def __init__(self, adapter: RealWorkflowAdapter | None = None) -> None:
        self._adapter = adapter
        self._planned_order: tuple[str, ...] = ()
        self._envelopes: dict[str, ExecutionEnvelope] = {}
        self._results: dict[str, ProviderResult] = {}
        self._observations: dict[str, ObservationEnvelope] = {}
        self._executions: dict[str, StepExecution] = {}
        self._workflow_id = ""

    @property
    def adapter(self) -> RealWorkflowAdapter | None:
        return self._adapter

    def is_live(self) -> bool:
        return self._adapter is not None

    def plan(self, pipeline: WorkflowPipeline) -> tuple[str, ...]:
        validated = validate_pipeline(pipeline)
        self._workflow_id = validated.workflow_id
        from .policy import topological_order
        self._planned_order = topological_order(validated.steps)
        return self._planned_order

    @property
    def planned_order(self) -> tuple[str, ...]:
        return self._planned_order

    def _require_adapter(self) -> RealWorkflowAdapter:
        if self._adapter is None:
            raise BackendUnavailableError(
                "no controlled real workflow adapter is configured; failing closed"
            )
        return self._adapter

    def _descriptor_for(
        self, step: WorkflowStep
    ) -> ProviderOperationDescriptor:
        adapter = self._require_adapter()
        descriptor = adapter.describe(step.capability_id, step.operation)
        if not isinstance(descriptor, ProviderOperationDescriptor):
            raise RealBackendContractError(
                "adapter.describe() must return ProviderOperationDescriptor"
            )
        if descriptor.capability_id != step.capability_id:
            raise RealBackendContractError(
                "provider capability id does not match the workflow step"
            )
        if descriptor.operation != step.operation:
            raise RealBackendContractError(
                "provider operation does not match the workflow step"
            )
        if descriptor.effect is not step.effect:
            raise RealBackendContractError(
                "provider effect does not match the workflow step"
            )
        if descriptor.requires_approval != step.requires_approval:
            raise RealBackendContractError(
                "provider approval requirement does not match the workflow step"
            )
        return descriptor

    def execute_step(
        self,
        step: WorkflowStep,
        *,
        workflow_id: str = "",
        inbound: Mapping[str, WorkflowArtifact] | None = None,
        approved: bool = False,
        epoch: int = 0,
    ) -> StepExecution:
        descriptor = self._descriptor_for(step)
        if step.requires_approval and not approved:
            raise WorkflowSecurityError(
                f"real step '{step.step_id}' requires an explicit human approval"
            )

        inbound = dict(inbound or {})
        envelope = ExecutionEnvelope.build(
            workflow_id=workflow_id or self._workflow_id,
            step=step,
            provider=descriptor.provider,
            network_policy=descriptor.network_policy,
            required_scopes=descriptor.required_scopes,
            inbound=inbound,
            approved=approved,
        )

        if envelope.idempotency_key in self._envelopes:
            raise WorkflowReplayError(
                f"real execution idempotency key already used for step '{step.step_id}'"
            )

        adapter = self._require_adapter()
        result = adapter.execute(envelope, step, inbound)
        if not isinstance(result, ProviderResult):
            raise RealBackendContractError(
                "adapter.execute() must return ProviderResult"
            )

        artifacts: list[WorkflowArtifact] = []
        declared = set(step.produces)
        returned = set(result.artifacts)
        if declared != returned:
            raise RealBackendContractError(
                f"provider artifacts do not match declared outputs: expected={sorted(declared)} got={sorted(returned)}"
            )

        trust = domain_trust(step.domain)
        for artifact_key, payload in sorted(result.artifacts.items()):
            kind = HandoffKind.TEXT
            if isinstance(payload, Mapping):
                kind = HandoffKind.STRUCTURED
            artifact = WorkflowArtifact(
                artifact_key=artifact_key,
                kind=kind,
                source_step=step.step_id,
                source_domain=step.domain,
                payload=payload,
                trust=trust,
                sha256=artifact_digest(payload),
            )
            artifacts.append(artifact)

        execution = StepExecution(
            step_id=step.step_id,
            capability_id=step.capability_id,
            operation=step.operation,
            status=(
                VerificationStatus.ACCEPTED
                if result.accepted
                else VerificationStatus.FAILED
            ),
            accepted=result.accepted,
            observed=False,
            verified=False,
            artifacts=tuple(artifacts),
            evidence={
                **dict(result.evidence),
                "provider": descriptor.provider,
                "provider_request_id": result.provider_request_id[:256],
                "execution_envelope": envelope.safe_dict(),
            },
            trust=trust,
            detail=(
                "provider accepted the request; independent observation is required"
                if result.accepted
                else result.detail or "provider rejected the request"
            ),
            digest=envelope.input_digest,
            epoch=epoch,
        )
        self._envelopes[envelope.idempotency_key] = envelope
        self._results[envelope.idempotency_key] = result
        self._executions[step.step_id] = execution
        return execution

    def observe_step(self, step_id: str) -> Mapping[str, Any]:
        execution = self._executions.get(step_id)
        if execution is None:
            raise WorkflowValidationError(
                f"no controlled-real execution exists for step: {step_id}"
            )
        envelope = self._envelopes.get(
            next(
                key
                for key, value in self._envelopes.items()
                if value.step_id == step_id
            )
        )
        adapter = self._require_adapter()
        observation = adapter.observe(envelope)
        if not isinstance(observation, ObservationEnvelope):
            raise RealBackendContractError(
                "adapter.observe() must return ObservationEnvelope"
            )
        self._observations[envelope.idempotency_key] = observation

        return {
            "step_id": step_id,
            "accepted": execution.accepted,
            "artifacts": {
                artifact.artifact_key: artifact.sha256
                for artifact in execution.artifacts
            },
            "observed": observation.observed,
            "state_digest": observation.state_digest,
            "observed_artifacts": dict(observation.artifact_digests),
            "evidence": dict(observation.evidence),
            "detail": observation.detail[:MAX_DETAIL_LENGTH],
        }

    def observe_workflow(self, workflow_id: str) -> WorkflowObservation:
        completed = tuple(
            sorted(
                step_id
                for step_id, execution in self._executions.items()
                if execution.accepted
            )
        )
        return WorkflowObservation(
            workflow_id=workflow_id or self._workflow_id,
            completed_steps=completed,
            artifact_keys=tuple(
                sorted(
                    key
                    for execution in self._executions.values()
                    for key in execution.safe_dict().get("artifacts", ())
                )
            ),
            detail="controlled real backend state; provider acceptance is not verification",
        )

    def transfer(self, handoff, artifact):
        raise BackendUnavailableError(
            "cross-domain real transfer is not enabled in M1; use the Phase 5 connector contract"
        )

    def create_draft(self, step, **kwargs):
        raise BackendUnavailableError(
            "real communication drafting is not enabled in M1"
        )


__all__ = [
    "ControlledRealWorkflowBackend",
    "ExecutionEnvelope",
    "ObservationEnvelope",
    "ProviderOperationDescriptor",
    "ProviderResult",
    "RealBackendContractError",
    "RealNetworkPolicy",
    "RealWorkflowAdapter",
]
