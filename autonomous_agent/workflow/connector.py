"""Bounded cross-domain workflow connector -- the safe M2 execution surface.

The connector is the only thing that is allowed to move a validated workflow
forward, and it does so by composing, never by bypassing:

* the M1 **policy** decides what is structurally and securely legal;
* the **backend** performs bounded, deterministic work and nothing else;
* the **session** owns progress, budget, approvals and depth;
* the **target resolver** turns semantic names into checked references;
* the **replay protector** refuses to redo finished work after a resume;
* the **prompt-injection guard** keeps untrusted content marked as data.

Three invariants are worth stating explicitly because they are what make a
cross-domain workflow safe rather than merely convenient:

1. **Authority never travels with data.** Approvals come from
   :meth:`grant_approval`, which only a caller can invoke. No payload, no
   artifact, no handoff and no backend response can grant an approval, widen a
   capability, change a policy decision or reclassify an action.
2. **Acceptance is not verification.** A step is only ``VERIFIED`` when state
   was independently read back from the backend and matched what the step
   claimed to produce. Evidence is never fabricated to fill the gap.
3. **Drafting is not sending.** The connector can create deterministic draft
   state and deliberately exposes no delivery path at all.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

from ..prompt_injection_guard import PromptInjectionGuard, TrustLevel
from .backend import BaseWorkflowBackend, UnsupportedWorkflowBackend
from .models import (
    MAX_ARTIFACT_PAYLOAD_LENGTH,
    MAX_DRAFT_BODY_LENGTH,
    MAX_EVIDENCE_ENTRIES,
    MAX_PARAMETER_DEPTH,
    MAX_PARAMETER_ITEMS,
    UNTRUSTED_ENVELOPE_KEY,
    ActionBudget,
    CommunicationDraft,
    StepExecution,
    VerificationStatus,
    WorkflowApprovalError,
    WorkflowArtifact,
    WorkflowHandoffError,
    WorkflowObservation,
    WorkflowPipeline,
    WorkflowSecurityError,
    WorkflowState,
    WorkflowStateError,
    WorkflowStep,
    WorkflowValidationError,
    artifact_digest,
    is_untrusted_marked,
    redact_secret,
    redact_structure,
)
from .policy import (
    BLOCKED_UNTRUSTED_SINK_DOMAINS,
    lowest_trust,
    pipeline_execution_order,
    pipeline_trust_map,
    validate_pipeline,
)
from .replay import WorkflowReplayProtector
from .session import WorkflowSession
from .target import WorkflowTargetResolver


#: Keys in untrusted payload content that would, if ever honoured, let data
#: promote itself. They are recorded as injection signals and never obeyed.
_AUTHORITY_CLAIM_KEYS: frozenset[str] = frozenset({
    "approved",
    "approval",
    "requires_approval",
    "authorized",
    "authorization",
    "capability",
    "capabilities",
    "grant",
    "grants",
    "policy",
    "trust",
    "risk",
    "effect",
    "safe_autonomous",
})


class BoundedWorkflowConnector:
    """Safe, bounded connector for cross-domain workflow execution."""

    def __init__(
        self,
        backend: BaseWorkflowBackend | None = None,
        *,
        session: WorkflowSession | None = None,
        guard: PromptInjectionGuard | None = None,
        replay: WorkflowReplayProtector | None = None,
        action_budget: ActionBudget | None = None,
        known_capability_ids: Iterable[str] | None = None,
    ) -> None:
        self._backend = backend if backend is not None else UnsupportedWorkflowBackend()
        self._guard = guard or PromptInjectionGuard()
        self._replay = replay or WorkflowReplayProtector()
        self._resolver = WorkflowTargetResolver()
        self._session = session or WorkflowSession(action_budget=action_budget)
        self._known_capability_ids = tuple(known_capability_ids) if known_capability_ids else None
        self._pipeline: WorkflowPipeline | None = None
        self._artifacts: dict[str, WorkflowArtifact] = {}
        self._executions: dict[str, StepExecution] = {}
        self._drafts: dict[str, CommunicationDraft] = {}
        self._injection_signals: list[str] = []

    # -- introspection -----------------------------------------------------
    @property
    def backend(self) -> BaseWorkflowBackend:
        return self._backend

    @property
    def session(self) -> WorkflowSession:
        return self._session

    @property
    def replay_protector(self) -> WorkflowReplayProtector:
        return self._replay

    @property
    def resolver(self) -> WorkflowTargetResolver:
        return self._resolver

    @property
    def pipeline(self) -> WorkflowPipeline | None:
        return self._pipeline

    @property
    def artifacts(self) -> dict[str, WorkflowArtifact]:
        return dict(self._artifacts)

    @property
    def drafts(self) -> tuple[CommunicationDraft, ...]:
        return tuple(self._drafts[key] for key in sorted(self._drafts))

    @property
    def injection_signals(self) -> tuple[str, ...]:
        """Untrusted-content signals seen while moving data across domains."""
        return tuple(self._injection_signals)

    def is_live(self) -> bool:
        return not isinstance(self._backend, UnsupportedWorkflowBackend)

    # -- validation --------------------------------------------------------
    def validate(self, pipeline: WorkflowPipeline) -> WorkflowPipeline:
        """Validate a pipeline through M1 policy and bind it to this session."""
        validated = validate_pipeline(pipeline, self._known_capability_ids)
        self._session.ensure_open()
        self._session.bind(validated.workflow_id)

        # The workflow's declared budget is the ceiling this session enforces.
        if validated.action_budget < self._session.action_budget.limit:
            budget = ActionBudget(limit=validated.action_budget)
            budget.used = min(self._session.action_budget.used, budget.limit)
            self._session.action_budget = budget

        self._pipeline = validated
        self._session.note(f"validated:{validated.workflow_id}")
        return validated

    def execution_order(self) -> tuple[str, ...]:
        return pipeline_execution_order(self._require_pipeline())

    def trust_map(self) -> dict[str, TrustLevel]:
        return pipeline_trust_map(self._require_pipeline())

    # -- approvals ---------------------------------------------------------
    def grant_approval(self, step_id: str, *, approver: str) -> None:
        """Record an explicit human approval. Only a caller can reach this."""
        pipeline = self._require_pipeline()
        clean_approver = str(approver).strip()
        if not clean_approver:
            raise WorkflowApprovalError("an approval must name the approving human")
        step = self._resolver._require_step(pipeline, step_id)
        self._session.grant_approval(step.step_id)
        self._session.note(f"approval_by:{redact_secret(clean_approver)[:64]}")

    # -- inspection --------------------------------------------------------
    def observe_workflow(self) -> WorkflowObservation:
        """Report observed workflow state without advancing anything."""
        pipeline = self._require_pipeline()
        completed = self._session.completed_steps
        verified = self._session.verified_steps
        pending = tuple(step.step_id for step in pipeline.steps if step.step_id not in completed)
        return WorkflowObservation(
            workflow_id=pipeline.workflow_id,
            session_id=self._session.session_id,
            session_state=self._session.state,
            workflow_state=self._workflow_state(pending, completed, verified),
            current_step=self._session.current_step,
            completed_steps=completed,
            verified_steps=verified,
            pending_steps=pending,
            artifact_keys=tuple(sorted(self._artifacts)),
            budget_limit=self._session.action_budget.limit,
            budget_used=self._session.action_budget.used,
            epoch=self._session.epoch,
            detail=f"backend={self._backend.name}",
        )

    def observe_step(self, step_id: str) -> dict[str, Any]:
        """Report observed state for one step, including backend read-back."""
        pipeline = self._require_pipeline()
        step = self._resolver._require_step(pipeline, step_id)
        execution = self._executions.get(step.step_id)
        backend_state: Any = None
        if execution is not None:
            try:
                backend_state = self._backend.observe_step(step.step_id)
            except Exception as exc:  # observation must never crash inspection
                backend_state = {"error": redact_secret(str(exc))[:200]}
        return {
            "step_id": step.step_id,
            "domain": step.domain_value,
            "capability_id": step.capability_id,
            "operation": step.operation,
            "effect": step.effect.value,
            "requires_approval": step.requires_approval,
            "approved": self._session.is_approved(step.step_id),
            "completed": self._session.is_completed(step.step_id),
            "verified": self._session.is_verified(step.step_id),
            "status": execution.status.value if execution else VerificationStatus.FAILED.value,
            "execution": execution.safe_dict() if execution else None,
            "backend_state": redact_structure(backend_state) if backend_state is not None else None,
        }

    def verify_workflow(self) -> WorkflowObservation:
        """Report completion, which requires every step to be verified."""
        observation = self.observe_workflow()
        if observation.complete:
            self._session.note("workflow_verified")
        return observation

    # -- execution ---------------------------------------------------------
    def execute_step(self, step_id: str) -> StepExecution:
        """Execute one validated step through the backend, then verify it."""
        pipeline = self._require_pipeline()
        self._session.ensure_open()
        target = self._resolver.resolve_step(pipeline, step_id, epoch=self._session.epoch)
        step = self._resolver.ensure_current(target, pipeline, epoch=self._session.epoch)

        if self._session.is_verified(step.step_id):
            raise WorkflowStateError(
                f"step '{step.step_id}' is already verified; a resume must not restart it"
            )

        self._assert_dependencies_ready(step)
        inbound = self._inbound_artifacts(step)

        if step.requires_approval and not self._session.is_approved(step.step_id):
            raise WorkflowApprovalError(
                f"step '{step.step_id}' requires an explicit human approval before execution"
            )

        step_key = self._replay.step_key(workflow_id=pipeline.workflow_id, step=step, inbound=inbound)
        self._replay.check(step_key, f"step {step.step_id}")
        mutation_key = ""
        if step.mutating:
            mutation_key = self._replay.mutation_key(
                workflow_id=pipeline.workflow_id, step=step, inbound=inbound
            )
            self._replay.check(mutation_key, f"mutation {step.step_id}")

        self._session.enter_step(step.step_id)
        try:
            self._session.consume_action(step.action_cost)
            execution = self._backend.execute_step(
                step,
                workflow_id=pipeline.workflow_id,
                inbound=inbound,
                approved=self._session.is_approved(step.step_id),
                epoch=self._session.epoch,
            )
            if not isinstance(execution, StepExecution):
                raise WorkflowValidationError("backend returned a non-StepExecution result")

            # A mutation may have landed even if verification later fails, so
            # its identity is recorded immediately: never repeat it blindly.
            if step.mutating:
                self._replay.record(mutation_key)

            verified = self._verify_execution(step, execution)
        finally:
            self._session.exit_step()

        if not verified.verified:
            self._executions[step.step_id] = verified
            return verified

        self._replay.record(step_key)
        for artifact in verified.artifacts:
            self._artifacts[artifact.artifact_key] = artifact
            self._session.record_artifact(artifact.artifact_key, artifact.sha256)
        self._session.record_completed(step.step_id, verified.digest)
        self._session.record_verified(step.step_id)
        self._executions[step.step_id] = verified
        return verified

    def _verify_execution(self, step: WorkflowStep, execution: StepExecution) -> StepExecution:
        """Independently observe backend state; only then may a step be VERIFIED."""
        from dataclasses import replace

        if not execution.accepted:
            return replace(
                execution,
                status=VerificationStatus.FAILED,
                observed=False,
                verified=False,
                detail="backend did not accept the step",
            )

        try:
            observed = self._backend.observe_step(step.step_id)
        except Exception as exc:
            return replace(
                execution,
                status=VerificationStatus.ACCEPTED,
                observed=False,
                verified=False,
                detail=f"independent observation failed: {redact_secret(str(exc))[:120]}",
            )

        observed_artifacts = dict((observed or {}).get("artifacts", {}) or {})
        declared = set(step.produces)
        produced = {artifact.artifact_key: artifact.sha256 for artifact in execution.artifacts}

        if declared != set(produced):
            return replace(
                execution,
                status=VerificationStatus.OBSERVED,
                observed=True,
                verified=False,
                detail="produced artifacts do not match the declared outputs",
            )
        for key, digest in produced.items():
            if observed_artifacts.get(key) != digest:
                return replace(
                    execution,
                    status=VerificationStatus.OBSERVED,
                    observed=True,
                    verified=False,
                    detail=f"observed digest for artifact '{key}' does not match the claimed evidence",
                )
        if not execution.has_evidence and declared:
            return replace(
                execution,
                status=VerificationStatus.OBSERVED,
                observed=True,
                verified=False,
                detail="no evidence accompanied the execution",
            )

        return replace(
            execution,
            status=VerificationStatus.VERIFIED,
            observed=True,
            verified=True,
            detail="state independently observed and matched the claimed evidence",
        )

    # -- cross-domain handoff ---------------------------------------------
    def handoff(self, source_step: str, target_step: str, artifact_key: str) -> WorkflowArtifact:
        """Move one declared artifact across a domain boundary, safely."""
        pipeline = self._require_pipeline()
        self._session.ensure_open()

        # 1 + 2: both endpoints must be declared steps of this workflow, and
        # the edge itself must be a handoff the policy already validated.
        target = self._resolver.resolve_handoff(
            pipeline, source_step, target_step, artifact_key, epoch=self._session.epoch
        )
        consumer = self._resolver.ensure_current(target, pipeline, epoch=self._session.epoch)
        producer = self._resolver._require_step(pipeline, source_step)
        handoff = self._declared_handoff(producer.step_id, consumer.step_id, target.artifact_key)

        if not self._session.is_verified(producer.step_id):
            raise WorkflowHandoffError(
                f"source step '{producer.step_id}' must be verified before its artifact can move"
            )
        artifact = self._artifacts.get(target.artifact_key)
        if artifact is None:
            raise WorkflowHandoffError(
                f"artifact '{target.artifact_key}' has not been produced yet"
            )

        # 3: payload bounds.
        self._assert_payload_bounds(artifact)

        # 5: trust can only move downwards, never upwards.
        inbound_trust = lowest_trust(artifact.trust, handoff.trust)
        if inbound_trust is TrustLevel.EXTERNAL and consumer.domain in BLOCKED_UNTRUSTED_SINK_DOMAINS:
            raise WorkflowSecurityError(
                f"untrusted content may never be handed to '{consumer.domain_value}'"
            )

        # 4 + 6: redact, and keep untrusted content visibly quarantined.
        moved = self._backend.transfer(handoff, artifact)
        moved = self._enforce_trust_floor(moved, inbound_trust)
        self._record_authority_claims(moved, consumer)

        # 7: deterministic digest of exactly what crossed the boundary.
        payload_digest = moved.sha256 or artifact_digest(moved.payload)
        handoff_key = self._replay.handoff_key(
            workflow_id=pipeline.workflow_id, handoff=handoff, payload_digest=payload_digest
        )
        # 10: replay protection.
        self._replay.check(handoff_key, f"handoff {producer.step_id}->{consumer.step_id}")

        # 8: the transfer itself costs budget.
        self._session.consume_action(1)

        # 9 + 10: record it in session state and in the replay protector.
        self._session.record_handoff(target.target_id, handoff_key)
        self._replay.record(handoff_key)
        self._artifacts[moved.artifact_key] = moved
        self._session.record_artifact(moved.artifact_key, payload_digest)
        return moved

    def _enforce_trust_floor(self, artifact: WorkflowArtifact, floor: TrustLevel) -> WorkflowArtifact:
        """A backend can never hand back content more trusted than it received."""
        from dataclasses import replace

        settled = lowest_trust(artifact.trust, floor)
        if settled is not artifact.trust:
            artifact = replace(artifact, trust=settled)
        if settled is TrustLevel.EXTERNAL and not is_untrusted_marked(artifact.payload):
            # The marker is never stripped in transit; restore it if a backend
            # handed back bare content.
            wrapped = artifact.wrapped_payload()
            artifact = replace(artifact, payload=wrapped, sha256=artifact_digest(wrapped))
        return artifact

    def _record_authority_claims(self, artifact: WorkflowArtifact, consumer: WorkflowStep) -> None:
        """Note attempts by payload content to promote itself, and obey none of them."""
        source = (
            artifact.source_domain.value
            if hasattr(artifact.source_domain, "value")
            else str(artifact.source_domain)
        )
        signals: list[str] = []
        self._scan_authority_claims(artifact.payload, artifact.trust, source, signals, depth=0)
        for signal in signals:
            entry = f"{consumer.step_id}:{signal}"
            if entry not in self._injection_signals:
                self._injection_signals.append(entry)

    def _scan_authority_claims(
        self,
        value: Any,
        trust: TrustLevel,
        source: str,
        signals: list[str],
        *,
        depth: int,
    ) -> None:
        """Walk a bounded payload looking for self-granted authority and injection.

        The walk descends through the untrusted envelope on purpose: quarantined
        content is exactly where a claim like ``{"approved": true}`` hides. Every
        hit is recorded as a signal and nothing more -- the connector reads
        approvals from the session, never from data.
        """
        if depth > MAX_PARAMETER_DEPTH or len(signals) >= MAX_EVIDENCE_ENTRIES:
            return
        if isinstance(value, Mapping):
            for key, nested in list(value.items())[:MAX_PARAMETER_ITEMS]:
                name = str(key).strip().lower()
                if name == UNTRUSTED_ENVELOPE_KEY:
                    continue  # the quarantine marker itself, not a claim
                if name in _AUTHORITY_CLAIM_KEYS:
                    signals.append(f"authority_claim:{key}")
                self._scan_authority_claims(nested, trust, source, signals, depth=depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for item in list(value)[:MAX_PARAMETER_ITEMS]:
                self._scan_authority_claims(item, trust, source, signals, depth=depth + 1)
            return
        if isinstance(value, str):
            result = self._guard.inspect(value, source=source, trust=trust)
            for name in result.signals:
                marker = f"injection:{name}"
                if marker not in signals:
                    signals.append(marker)

    # -- communication drafts ---------------------------------------------
    def create_draft(
        self,
        step_id: str,
        *,
        channel: str = "email",
        recipients: Sequence[str] = (),
        subject: str = "",
        body: str = "",
    ) -> CommunicationDraft:
        """Create deterministic draft state for a communication step.

        This is the end of the line in M2: there is no send, deliver, dispatch
        or transmit path anywhere in this layer.
        """
        pipeline = self._require_pipeline()
        self._session.ensure_open()
        step = self._resolver._require_step(pipeline, step_id)

        if step.requires_approval and not self._session.is_approved(step.step_id):
            raise WorkflowApprovalError(
                f"step '{step.step_id}' requires an explicit human approval before drafting"
            )
        if len(str(body)) > MAX_DRAFT_BODY_LENGTH:
            raise WorkflowValidationError(
                f"draft body exceeds max length: {len(str(body))} > {MAX_DRAFT_BODY_LENGTH}"
            )

        trust = self.trust_map().get(step.step_id, TrustLevel.EXTERNAL)
        for key in step.consumes:
            artifact = self._artifacts.get(key)
            if artifact is not None:
                trust = lowest_trust(trust, artifact.trust)

        self._session.consume_action(1)
        draft = self._backend.create_draft(
            step,
            channel=channel,
            recipients=recipients,
            subject=subject,
            body=body,
            trust=trust,
        )
        if draft.sent:  # pragma: no cover - defensive, the property is constant
            raise WorkflowSecurityError("a draft must never report itself as sent")
        self._drafts[draft.draft_id] = draft
        self._session.note(f"draft:{draft.draft_id}")
        return draft

    # -- internals ---------------------------------------------------------
    def _require_pipeline(self) -> WorkflowPipeline:
        if self._pipeline is None:
            raise WorkflowStateError("no validated workflow is bound to this connector")
        return self._pipeline

    def _declared_handoff(self, source_step: str, target_step: str, artifact_key: str):
        pipeline = self._require_pipeline()
        for handoff in pipeline.handoffs:
            if (
                handoff.source_step == source_step
                and handoff.target_step == target_step
                and handoff.artifact_key == artifact_key
            ):
                return handoff
        raise WorkflowHandoffError(
            f"no declared handoff '{artifact_key}' from '{source_step}' to '{target_step}'"
        )

    def _assert_dependencies_ready(self, step: WorkflowStep) -> None:
        for dependency in step.depends_on:
            if not self._session.is_verified(dependency):
                raise WorkflowStateError(
                    f"step '{step.step_id}' cannot run before '{dependency}' is verified"
                )

    def _inbound_artifacts(self, step: WorkflowStep) -> dict[str, WorkflowArtifact]:
        inbound: dict[str, WorkflowArtifact] = {}
        for key in step.consumes:
            artifact = self._artifacts.get(key)
            if artifact is None:
                raise WorkflowHandoffError(
                    f"step '{step.step_id}' consumes artifact '{key}', which has not been handed over"
                )
            inbound[key] = artifact
        return inbound

    def _assert_payload_bounds(self, artifact: WorkflowArtifact) -> None:
        encoded = json.dumps(redact_structure(artifact.payload), sort_keys=True, default=str)
        if len(encoded) > MAX_ARTIFACT_PAYLOAD_LENGTH:
            raise WorkflowSecurityError(
                f"handoff payload exceeds the bound: {len(encoded)} > {MAX_ARTIFACT_PAYLOAD_LENGTH}"
            )

    def _workflow_state(
        self,
        pending: tuple[str, ...],
        completed: tuple[str, ...],
        verified: tuple[str, ...],
    ) -> WorkflowState:
        if not completed and not self._executions:
            return WorkflowState.VALIDATED
        if pending:
            return WorkflowState.RUNNING
        return WorkflowState.COMPLETED if set(completed) == set(verified) else WorkflowState.BLOCKED


__all__ = ["BoundedWorkflowConnector"]
