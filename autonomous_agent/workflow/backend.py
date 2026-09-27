"""Backends for the bounded cross-domain workflow domain.

- :class:`BaseWorkflowBackend` defines the safe, structured execution surface.
- :class:`MockWorkflowBackend` is a deterministic in-memory backend for CI and tests.
- :class:`UnsupportedWorkflowBackend` is the default when no safe live workflow
  execution backend is configured.

Nothing in this module runs a shell, spawns a process, evaluates code, opens a
socket, reads an environment credential or changes an authorization decision.
A backend is a *state machine over declared artifacts*: it accepts a validated
step, produces deterministic evidence, and reports honestly when it cannot.

The mock backend is deterministic by construction: every value it produces is
derived from the workflow id, the step declaration and the inbound artifacts
through SHA-256. It never reads the clock, never uses randomness and never
depends on iteration order, so the same pipeline always yields the same
digests in CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..prompt_injection_guard import PromptInjectionGuard, TrustLevel
from .models import (
    MAX_DRAFT_BODY_LENGTH,
    MAX_DRAFT_RECIPIENTS,
    MAX_DRAFT_SUBJECT_LENGTH,
    BackendUnavailableError,
    CommunicationDraft,
    HandoffKind,
    StepEffect,
    StepExecution,
    VerificationStatus,
    WorkflowArtifact,
    WorkflowHandoff,
    WorkflowObservation,
    WorkflowPipeline,
    WorkflowSecurityError,
    WorkflowStep,
    WorkflowValidationError,
    artifact_digest,
    redact_secret,
    redact_structure,
)
from .policy import (
    domain_trust,
    lowest_trust,
    validate_pipeline,
)


#: Operations that would cross from drafting into delivery. The invariant is
#: ``draft != send``: this layer models drafts only, and delivery stays a
#: separately authorized, separately approved operation elsewhere.
DELIVERY_OPERATIONS: frozenset[str] = frozenset({
    "send",
    "send_email",
    "deliver",
    "dispatch",
    "post",
    "transmit",
    "publish",
    "submit",
})

#: Channels the mock backend can model a draft for.
DRAFT_CHANNELS: frozenset[str] = frozenset({"email", "calendar", "message", "comment"})


class BaseWorkflowBackend:
    """The safe, structured cross-domain workflow execution surface.

    Subclasses may model or drive real domain work, but the contract forbids
    arbitrary shell execution, arbitrary process launching, arbitrary script
    evaluation, authorization changes and secret persistence.
    """

    name = "base"

    def plan(self, pipeline: WorkflowPipeline) -> tuple[str, ...]:
        raise BackendUnavailableError("base backend cannot plan workflows")

    def execute_step(
        self,
        step: WorkflowStep,
        *,
        workflow_id: str,
        inbound: Mapping[str, WorkflowArtifact] | None = None,
        approved: bool = False,
        epoch: int = 0,
    ) -> StepExecution:
        raise BackendUnavailableError("base backend cannot execute workflow steps")

    def transfer(
        self,
        handoff: WorkflowHandoff,
        artifact: WorkflowArtifact,
    ) -> WorkflowArtifact:
        raise BackendUnavailableError("base backend cannot transfer workflow artifacts")

    def observe_step(self, step_id: str) -> Mapping[str, Any]:
        raise BackendUnavailableError("base backend cannot observe workflow steps")

    def observe_workflow(self, workflow_id: str) -> WorkflowObservation:
        raise BackendUnavailableError("base backend cannot observe workflow state")

    def create_draft(
        self,
        step: WorkflowStep,
        *,
        channel: str,
        recipients: Sequence[str] = (),
        subject: str = "",
        body: str = "",
        trust: TrustLevel = TrustLevel.TOOL_RESULT,
    ) -> CommunicationDraft:
        raise BackendUnavailableError("base backend cannot create communication drafts")


class UnsupportedWorkflowBackend(BaseWorkflowBackend):
    """Default backend: every operation fails closed, nothing is fabricated."""

    name = "unsupported"

    _MESSAGE = "no safe cross-domain workflow backend is available in this environment"

    def plan(self, pipeline):
        raise BackendUnavailableError(self._MESSAGE)

    def execute_step(self, step, *, workflow_id="", inbound=None, approved=False, epoch=0):
        raise BackendUnavailableError(self._MESSAGE)

    def transfer(self, handoff, artifact):
        raise BackendUnavailableError(self._MESSAGE)

    def observe_step(self, step_id):
        raise BackendUnavailableError(self._MESSAGE)

    def observe_workflow(self, workflow_id):
        raise BackendUnavailableError(self._MESSAGE)

    def create_draft(self, step, *, channel="", recipients=(), subject="", body="", trust=TrustLevel.TOOL_RESULT):
        raise BackendUnavailableError(self._MESSAGE)


@dataclass
class _MockStepState:
    """In-memory record of one simulated step."""

    step_id: str
    status: VerificationStatus = VerificationStatus.FAILED
    artifacts: dict[str, WorkflowArtifact] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    epoch: int = 0


class MockWorkflowBackend(BaseWorkflowBackend):
    """Deterministic, in-memory cross-domain workflow backend for CI and tests.

    It simulates planning, read-only steps, controlled-write steps, artifact
    creation, cross-domain transfer and communication *draft* state. It sends
    nothing, calls nothing external and fabricates no live success.
    """

    name = "mock"

    def __init__(self, *, guard: PromptInjectionGuard | None = None) -> None:
        self._guard = guard or PromptInjectionGuard()
        self._steps: dict[str, _MockStepState] = {}
        self._drafts: dict[str, CommunicationDraft] = {}
        self._workflow_id = ""
        self._order: tuple[str, ...] = ()

    # -- planning ----------------------------------------------------------
    def plan(self, pipeline: WorkflowPipeline) -> tuple[str, ...]:
        """Validate the pipeline through M1 policy and return the execution order."""
        validated = validate_pipeline(pipeline)
        self._workflow_id = validated.workflow_id
        from .policy import topological_order  # local import keeps the module graph flat

        self._order = topological_order(validated.steps)
        return self._order

    @property
    def planned_order(self) -> tuple[str, ...]:
        return self._order

    # -- execution ---------------------------------------------------------
    def execute_step(
        self,
        step: WorkflowStep,
        *,
        workflow_id: str = "",
        inbound: Mapping[str, WorkflowArtifact] | None = None,
        approved: bool = False,
        epoch: int = 0,
    ) -> StepExecution:
        """Deterministically simulate one validated step."""
        if not isinstance(step, WorkflowStep):
            raise WorkflowValidationError("execute_step requires a WorkflowStep")

        operation = str(step.operation).strip().lower()
        if operation in DELIVERY_OPERATIONS or operation.rsplit(".", 1)[-1] in DELIVERY_OPERATIONS:
            raise WorkflowSecurityError(
                f"autonomous delivery is not implemented: '{step.operation}' would send rather than draft"
            )
        if step.mutating and not approved:
            raise WorkflowSecurityError(
                f"controlled-write step '{step.step_id}' requires an explicit approval before execution"
            )

        inbound_artifacts = dict(inbound or {})
        for key, artifact in inbound_artifacts.items():
            if not isinstance(artifact, WorkflowArtifact):
                raise WorkflowValidationError(f"inbound artifact '{key}' must be a WorkflowArtifact")

        # Trust is inherited from the step's own domain and every inbound
        # artifact, and can only ever move downwards.
        trust = lowest_trust(
            domain_trust(step.domain),
            *[artifact.trust for artifact in inbound_artifacts.values()],
        )

        inputs_digest = artifact_digest(
            {
                "workflow_id": workflow_id,
                "step_id": step.step_id,
                "capability_id": step.capability_id,
                "operation": operation,
                "parameters": redact_structure(dict(step.parameters)),
                "inbound": {key: value.sha256 or artifact_digest(value.payload) for key, value in sorted(inbound_artifacts.items())},
            }
        )

        artifacts: list[WorkflowArtifact] = []
        for artifact_key in step.produces:
            payload = self._simulated_payload(step, artifact_key, inputs_digest, inbound_artifacts)
            artifacts.append(
                WorkflowArtifact(
                    artifact_key=artifact_key,
                    kind=self._artifact_kind(step),
                    source_step=step.step_id,
                    source_domain=step.domain,
                    payload=payload,
                    trust=trust,
                    sha256=artifact_digest(payload),
                )
            )

        evidence: dict[str, Any] = {
            "workflow_id": workflow_id,
            "step_id": step.step_id,
            "capability_id": step.capability_id,
            "operation": operation,
            "effect": step.effect.value if isinstance(step.effect, StepEffect) else str(step.effect),
            "domain": step.domain_value,
            "inputs_digest": inputs_digest,
            "produced": [artifact.artifact_key for artifact in artifacts],
            "consumed": list(step.consumes),
            "approved": bool(approved),
        }

        state = _MockStepState(
            step_id=step.step_id,
            status=VerificationStatus.ACCEPTED,
            artifacts={artifact.artifact_key: artifact for artifact in artifacts},
            evidence=evidence,
            epoch=epoch,
        )
        self._steps[step.step_id] = state

        return StepExecution(
            step_id=step.step_id,
            capability_id=step.capability_id,
            operation=operation,
            status=VerificationStatus.ACCEPTED,
            accepted=True,
            observed=False,
            verified=False,
            artifacts=tuple(artifacts),
            evidence=evidence,
            trust=trust,
            detail="backend accepted the step; independent observation is still required",
            digest=inputs_digest,
            epoch=epoch,
        )

    def _artifact_kind(self, step: WorkflowStep) -> HandoffKind:
        operation = str(step.operation).lower()
        if "table" in operation:
            return HandoffKind.TABLE
        if "observe" in operation or "inspect" in operation:
            return HandoffKind.OBSERVATION
        if "metadata" in operation:
            return HandoffKind.METADATA
        return HandoffKind.TEXT

    def _simulated_payload(
        self,
        step: WorkflowStep,
        artifact_key: str,
        inputs_digest: str,
        inbound: Mapping[str, WorkflowArtifact],
    ) -> dict[str, Any]:
        """Deterministic, secret-free, obviously-simulated artifact payload."""
        return {
            "simulated": True,
            "artifact_key": artifact_key,
            "produced_by": step.step_id,
            "domain": step.domain_value,
            "operation": step.operation,
            "inputs_digest": inputs_digest,
            "consumed_artifacts": sorted(inbound),
        }

    # -- observation -------------------------------------------------------
    def observe_step(self, step_id: str) -> Mapping[str, Any]:
        """Independently read back what the backend recorded for a step."""
        state = self._steps.get(step_id)
        if state is None:
            raise WorkflowValidationError(f"no backend state recorded for step: {step_id}")
        return {
            "step_id": state.step_id,
            "status": state.status.value,
            "artifacts": {key: value.sha256 for key, value in sorted(state.artifacts.items())},
            "evidence": redact_structure(dict(state.evidence)),
            "epoch": state.epoch,
        }

    def observe_workflow(self, workflow_id: str) -> WorkflowObservation:
        completed = tuple(sorted(self._steps))
        return WorkflowObservation(
            workflow_id=workflow_id or self._workflow_id,
            completed_steps=completed,
            artifact_keys=tuple(
                sorted({key for state in self._steps.values() for key in state.artifacts})
            ),
            detail="deterministic mock backend state",
        )

    # -- cross-domain transfer --------------------------------------------
    def transfer(self, handoff: WorkflowHandoff, artifact: WorkflowArtifact) -> WorkflowArtifact:
        """Move an artifact across a domain boundary without ever upgrading its trust."""
        if not isinstance(handoff, WorkflowHandoff):
            raise WorkflowValidationError("transfer requires a WorkflowHandoff")
        if not isinstance(artifact, WorkflowArtifact):
            raise WorkflowValidationError("transfer requires a WorkflowArtifact")
        if artifact.artifact_key != handoff.artifact_key:
            raise WorkflowValidationError(
                f"artifact '{artifact.artifact_key}' does not match handoff key '{handoff.artifact_key}'"
            )

        trust = lowest_trust(artifact.trust, handoff.trust)
        payload = artifact.wrapped_payload() if trust is TrustLevel.EXTERNAL else redact_structure(artifact.payload)
        return WorkflowArtifact(
            artifact_key=artifact.artifact_key,
            kind=handoff.kind,
            source_step=artifact.source_step,
            source_domain=artifact.source_domain,
            payload=payload,
            trust=trust,
            sha256=artifact_digest(payload),
        )

    # -- communication drafts ---------------------------------------------
    def create_draft(
        self,
        step: WorkflowStep,
        *,
        channel: str = "email",
        recipients: Sequence[str] = (),
        subject: str = "",
        body: str = "",
        trust: TrustLevel = TrustLevel.TOOL_RESULT,
    ) -> CommunicationDraft:
        """Create deterministic draft state. This never sends anything."""
        if not isinstance(step, WorkflowStep):
            raise WorkflowValidationError("create_draft requires a WorkflowStep")
        clean_channel = str(channel).strip().lower()
        if clean_channel not in DRAFT_CHANNELS:
            raise WorkflowValidationError(f"unsupported draft channel: {channel!r}")
        if not isinstance(recipients, (list, tuple)):
            raise WorkflowValidationError("draft recipients must be a list or tuple")
        if len(recipients) > MAX_DRAFT_RECIPIENTS:
            raise WorkflowValidationError(
                f"draft exceeds max recipients: {len(recipients)} > {MAX_DRAFT_RECIPIENTS}"
            )
        clean_recipients = tuple(str(item).strip()[:320] for item in recipients if str(item).strip())

        safe_subject = redact_secret(str(subject))[:MAX_DRAFT_SUBJECT_LENGTH]
        safe_body = redact_secret(str(body))[:MAX_DRAFT_BODY_LENGTH]
        if trust is TrustLevel.EXTERNAL:
            # Untrusted source material stays visibly quarantined inside the draft.
            safe_body = self._guard.wrap(safe_body, source=step.domain_value, trust=TrustLevel.EXTERNAL)

        digest = artifact_digest(
            {
                "channel": clean_channel,
                "recipients": sorted(clean_recipients),
                "subject": safe_subject,
                "body": safe_body,
                "source_step": step.step_id,
            }
        )
        draft = CommunicationDraft(
            draft_id=f"draft-{step.step_id}",
            channel=clean_channel,
            recipients=clean_recipients,
            subject=safe_subject,
            body=safe_body,
            source_step=step.step_id,
            trust=trust,
            digest=digest,
        )
        self._drafts[draft.draft_id] = draft
        return draft

    def get_draft(self, draft_id: str) -> CommunicationDraft | None:
        return self._drafts.get(draft_id)

    @property
    def drafts(self) -> tuple[CommunicationDraft, ...]:
        return tuple(self._drafts[key] for key in sorted(self._drafts))

    def reset(self) -> None:
        self._steps.clear()
        self._drafts.clear()
        self._order = ()
        self._workflow_id = ""


__all__ = [
    "DELIVERY_OPERATIONS",
    "DRAFT_CHANNELS",
    "BaseWorkflowBackend",
    "MockWorkflowBackend",
    "UnsupportedWorkflowBackend",
]
