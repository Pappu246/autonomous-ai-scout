"""Phase 6 M1 contract tests for controlled real workflow execution."""

from __future__ import annotations

from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.workflow import (
    StepEffect,
    VerificationStatus,
    WorkflowArtifact,
    WorkflowPipeline,
    WorkflowSecurityError,
    WorkflowStep,
)
from autonomous_agent.workflow.models import BackendUnavailableError, WorkflowReplayError
from autonomous_agent.workflow.real_backend import (
    ControlledRealWorkflowBackend,
    ExecutionEnvelope,
    ObservationEnvelope,
    ProviderOperationDescriptor,
    ProviderResult,
    RealBackendContractError,
    RealNetworkPolicy,
)


def _step(
    *,
    step_id: str = "research",
    operation: str = "search",
    capability_id: str = "web:search",
    effect: StepEffect = StepEffect.READ_ONLY,
    requires_approval: bool = False,
) -> WorkflowStep:
    return WorkflowStep(
        step_id=step_id,
        domain=CapabilityDomain.WEB,
        capability_id=capability_id,
        operation=operation,
        produces=("result",),
        effect=effect,
        requires_approval=requires_approval,
    )


class FakeAdapter:
    name = "fake-provider"

    def __init__(self, *, effect: StepEffect = StepEffect.READ_ONLY, approval: bool = False):
        self.descriptor = ProviderOperationDescriptor(
            capability_id="web:search" if effect is StepEffect.READ_ONLY else "web:update",
            operation="search" if effect is StepEffect.READ_ONLY else "update",
            provider=self.name,
            network_policy=RealNetworkPolicy.BOUNDED_PROVIDER,
            required_scopes=("web.read",) if effect is StepEffect.READ_ONLY else ("web.write",),
            effect=effect,
            requires_approval=approval,
        )
        self.executions = []
        self.observations = 0
        self.last_artifacts = {}

    def describe(self, capability_id, operation):
        return self.descriptor

    def execute(self, envelope, step, inbound):
        self.executions.append(envelope)
        self.last_artifacts = {"result": {"value": "provider-output"}}
        return ProviderResult(
            accepted=True,
            artifacts=self.last_artifacts,
            evidence={"source": "fake-provider", "request_id": envelope.idempotency_key[:12]},
            provider_request_id="req-1",
            detail="accepted by test provider",
        )

    def observe(self, envelope):
        self.observations += 1
        artifact_digests = {
            key: __import__(
                "autonomous_agent.workflow.models",
                fromlist=["artifact_digest"],
            ).artifact_digest(value)
            for key, value in self.last_artifacts.items()
        }
        return ObservationEnvelope(
            observed=True,
            state_digest="state-1",
            artifact_digests=artifact_digests,
            evidence={"observed": True},
            detail="re-read by test provider",
        )


def _pipeline(step: WorkflowStep) -> WorkflowPipeline:
    return WorkflowPipeline(
        workflow_id="wf-m1",
        name="M1 contract test",
        steps=(step,),
        action_budget=10,
    )


def test_default_real_backend_fails_closed():
    backend = ControlledRealWorkflowBackend()
    assert backend.is_live() is False

    try:
        backend.execute_step(_step(), workflow_id="wf-m1")
    except BackendUnavailableError as exc:
        assert "failing closed" in str(exc)
    else:
        raise AssertionError("real backend without adapter must fail closed")


def test_execution_envelope_is_secret_free_and_deterministic():
    step = _step()
    envelope_one = ExecutionEnvelope.build(
        workflow_id="wf-m1",
        step=step,
        provider="fake-provider",
        network_policy=RealNetworkPolicy.BOUNDED_PROVIDER,
        required_scopes=("web.read",),
        approved=False,
    )
    envelope_two = ExecutionEnvelope.build(
        workflow_id="wf-m1",
        step=step,
        provider="fake-provider",
        network_policy=RealNetworkPolicy.BOUNDED_PROVIDER,
        required_scopes=("web.read",),
        approved=False,
    )

    assert envelope_one.safe_dict() == envelope_two.safe_dict()
    serialized = str(envelope_one.safe_dict()).lower()
    assert "parameters" not in serialized
    assert "secret" not in serialized
    assert envelope_one.idempotency_key == envelope_two.idempotency_key


def test_adapter_operation_descriptor_requires_observation_and_idempotency():
    try:
        ProviderOperationDescriptor(
            capability_id="web:search",
            operation="search",
            provider="fake-provider",
            observable=False,
        )
    except RealBackendContractError as exc:
        assert "observation path" in str(exc)
    else:
        raise AssertionError("non-observable real operations must be rejected")

    try:
        ProviderOperationDescriptor(
            capability_id="web:search",
            operation="search",
            provider="fake-provider",
            idempotent=False,
        )
    except RealBackendContractError as exc:
        assert "idempotent" in str(exc)
    else:
        raise AssertionError("non-idempotent M1 operations must be rejected")


def test_autonomous_delivery_is_not_an_allowed_real_adapter_operation():
    try:
        ProviderOperationDescriptor(
            capability_id="communication:send",
            operation="send",
            provider="fake-provider",
        )
    except WorkflowSecurityError as exc:
        assert "delivery" in str(exc)
    else:
        raise AssertionError("send must never be exposed by the real workflow contract")


def test_mutating_real_operation_requires_explicit_approval_contract():
    try:
        ProviderOperationDescriptor(
            capability_id="web:update",
            operation="update",
            provider="fake-provider",
            effect=StepEffect.MUTATING,
            requires_approval=False,
        )
    except WorkflowSecurityError as exc:
        assert "requires_approval" in str(exc)
    else:
        raise AssertionError("mutating provider operations must declare approval")


def test_real_backend_preserves_accepted_not_verified_boundary():
    adapter = FakeAdapter()
    backend = ControlledRealWorkflowBackend(adapter)
    pipeline = _pipeline(_step())
    assert backend.plan(pipeline) == ("research",)

    execution = backend.execute_step(
        pipeline.steps[0],
        workflow_id=pipeline.workflow_id,
    )

    assert execution.accepted is True
    assert execution.status is VerificationStatus.ACCEPTED
    assert execution.observed is False
    assert execution.verified is False
    assert execution.artifacts[0].artifact_key == "result"

    state = backend.observe_step("research")
    assert state["accepted"] is True
    assert state["observed"] is True
    assert state["observed_artifacts"]["result"] == execution.artifacts[0].sha256
    assert adapter.observations == 1


def test_backend_refuses_provider_capability_mismatch():
    adapter = FakeAdapter()
    backend = ControlledRealWorkflowBackend(adapter)

    mismatched = _step(
        capability_id="web:open",
        operation="open",
    )

    try:
        backend.execute_step(mismatched, workflow_id="wf-m1")
    except RealBackendContractError as exc:
        assert "capability id" in str(exc)
    else:
        raise AssertionError("provider descriptor mismatch must fail closed")


def test_backend_refuses_missing_artifact_declaration():
    adapter = FakeAdapter()
    backend = ControlledRealWorkflowBackend(adapter)
    step = _step()

    class BadAdapter(FakeAdapter):
        def execute(self, envelope, step, inbound):
            return ProviderResult(
                accepted=True,
                artifacts={"unexpected": "x"},
                evidence={"ok": True},
            )

    backend = ControlledRealWorkflowBackend(BadAdapter())
    try:
        backend.execute_step(step, workflow_id="wf-m1")
    except RealBackendContractError as exc:
        assert "declared outputs" in str(exc)
    else:
        raise AssertionError("undeclared provider artifacts must fail closed")


def test_provider_acceptance_cannot_be_replayed_by_same_backend():
    adapter = FakeAdapter()
    backend = ControlledRealWorkflowBackend(adapter)
    step = _step()

    first = backend.execute_step(step, workflow_id="wf-m1")
    assert first.accepted is True

    try:
        backend.execute_step(step, workflow_id="wf-m1")
    except WorkflowReplayError as exc:
        assert "idempotency key" in str(exc)
    else:
        raise AssertionError("duplicate provider execution must be blocked")
