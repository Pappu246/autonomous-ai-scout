"""Request adapter that binds the bounded workflow layer to the existing sandbox.

This module is the *only* translation point between JSON-shaped tool arguments
and the M1/M2 workflow objects. It deliberately contains no execution power of
its own:

* it parses a declarative pipeline mapping into validated M1 dataclasses;
* it dispatches one allowlisted operation onto an existing
  :class:`~autonomous_agent.workflow.connector.BoundedWorkflowConnector`;
* it returns a JSON-safe, redacted projection of what that connector reported.

Everything that decides *whether* an operation may happen -- policy, approval,
budget, replay, trust -- already lives in M1/M2 and is reached through the
connector, never re-implemented here. The sandbox calls
:func:`execute_workflow_operation`; the digital provider, planner and runtime
stay untouched because the capability reaches this code through the existing
``run_safe_operation`` boundary like every other domain.

There is no send/deliver/dispatch/transmit operation in the allowlist, and the
refusal list below states that explicitly so a future edit has to remove a
named guard rather than quietly add a path.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..digital.domains import CapabilityDomain, coerce_domain
from ..prompt_injection_guard import TrustLevel
from .connector import BoundedWorkflowConnector
from .models import (
    MAX_DRAFT_RECIPIENTS,
    MAX_METADATA_ENTRIES,
    MAX_WORKFLOW_HANDOFFS,
    MAX_WORKFLOW_STEPS,
    HandoffKind,
    StepEffect,
    WorkflowHandoff,
    WorkflowPipeline,
    WorkflowStep,
    WorkflowValidationError,
    redact_structure,
)

#: Operations the sandbox may dispatch onto a bounded workflow connector.
#: Read-only inspections are included because post-condition observation must
#: be able to re-read state independently of the call it is verifying.
WORKFLOW_SANDBOX_OPERATIONS: frozenset[str] = frozenset(
    {
        "plan",
        "handoff",
        "execute",
        "coordinate",
        "draft",
        "observe_workflow",
        "observe_step",
        "verify",
    }
)

#: Operations that must never exist here. Named explicitly so that adding a
#: delivery path requires deleting a guard, not just adding a branch.
REFUSED_WORKFLOW_OPERATIONS: frozenset[str] = frozenset(
    {
        "send",
        "send_draft",
        "send_email",
        "deliver",
        "delivery",
        "dispatch",
        "transmit",
        "publish",
        "post",
        "email_send",
        "message_send",
    }
)

#: Operations that mutate bounded workflow state and therefore need approval.
MUTATING_WORKFLOW_OPERATIONS: frozenset[str] = frozenset({"execute", "draft"})


class WorkflowRequestError(WorkflowValidationError):
    """A tool request could not be turned into a bounded workflow operation."""


# --------------------------------------------------------------------------
# Declarative pipeline parsing
# --------------------------------------------------------------------------
def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise WorkflowRequestError(f"workflow request is missing '{field}'")
        return ""
    if not isinstance(value, str):
        raise WorkflowRequestError(f"workflow request field '{field}' must be a string")
    cleaned = value.strip()
    if required and not cleaned:
        raise WorkflowRequestError(f"workflow request is missing '{field}'")
    return cleaned


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise WorkflowRequestError(f"workflow request field '{field}' must be a list of strings")
    return tuple(_text(item, field) for item in value)


def step_from_mapping(payload: Any) -> WorkflowStep:
    """Build one validated :class:`WorkflowStep` from a declarative mapping."""
    if not isinstance(payload, Mapping):
        raise WorkflowRequestError("each workflow step must be an object")
    try:
        domain = coerce_domain(payload.get("domain"))
    except (TypeError, ValueError) as exc:
        raise WorkflowRequestError(
            f"workflow step declares an unknown domain: {payload.get('domain')!r}"
        ) from exc
    effect_value = _text(payload.get("effect", StepEffect.READ_ONLY.value), "effect")
    try:
        effect = StepEffect(effect_value or StepEffect.READ_ONLY.value)
    except ValueError as exc:
        raise WorkflowRequestError(f"unknown workflow step effect: {effect_value}") from exc
    parameters = payload.get("parameters", {})
    if parameters in (None, ""):
        parameters = {}
    if not isinstance(parameters, Mapping):
        raise WorkflowRequestError("workflow step parameters must be an object")
    action_cost = payload.get("action_cost", 1)
    if isinstance(action_cost, bool) or not isinstance(action_cost, int):
        raise WorkflowRequestError("workflow step action_cost must be an integer")
    return WorkflowStep(
        step_id=_text(payload.get("step_id"), "step_id", required=True),
        domain=domain,
        capability_id=_text(payload.get("capability_id"), "capability_id", required=True),
        operation=_text(payload.get("operation"), "operation", required=True),
        depends_on=_string_tuple(payload.get("depends_on"), "depends_on"),
        parameters=dict(parameters),
        consumes=_string_tuple(payload.get("consumes"), "consumes"),
        produces=_string_tuple(payload.get("produces"), "produces"),
        effect=effect,
        requires_approval=bool(payload.get("requires_approval", False)),
        action_cost=action_cost,
        description=_text(payload.get("description"), "description"),
    )


def handoff_from_mapping(payload: Any) -> WorkflowHandoff:
    """Build one :class:`WorkflowHandoff` from a declarative mapping."""
    if not isinstance(payload, Mapping):
        raise WorkflowRequestError("each workflow handoff must be an object")
    kind_value = _text(payload.get("kind", HandoffKind.TEXT.value), "kind")
    try:
        kind = HandoffKind(kind_value or HandoffKind.TEXT.value)
    except ValueError as exc:
        raise WorkflowRequestError(f"unknown workflow handoff kind: {kind_value}") from exc
    trust_value = _text(payload.get("trust", TrustLevel.TOOL_RESULT.value), "trust")
    try:
        trust = TrustLevel(trust_value or TrustLevel.TOOL_RESULT.value)
    except ValueError as exc:
        raise WorkflowRequestError(f"unknown workflow handoff trust: {trust_value}") from exc
    return WorkflowHandoff(
        source_step=_text(payload.get("source_step"), "source_step", required=True),
        target_step=_text(payload.get("target_step"), "target_step", required=True),
        artifact_key=_text(payload.get("artifact_key"), "artifact_key", required=True),
        kind=kind,
        trust=trust,
        description=_text(payload.get("description"), "description"),
    )


def pipeline_from_mapping(payload: Any) -> WorkflowPipeline:
    """Build an unvalidated :class:`WorkflowPipeline` from a declarative mapping.

    Structural safety is *not* decided here: the pipeline is handed to the M1
    validator through the connector, which is the single authority on whether
    it may run.
    """
    if not isinstance(payload, Mapping):
        raise WorkflowRequestError("workflow pipeline must be an object")
    steps = payload.get("steps", ())
    if isinstance(steps, str) or not isinstance(steps, Sequence):
        raise WorkflowRequestError("workflow pipeline steps must be a list")
    if len(steps) > MAX_WORKFLOW_STEPS:
        raise WorkflowRequestError(
            f"workflow pipeline declares too many steps: {len(steps)} > {MAX_WORKFLOW_STEPS}"
        )
    handoffs = payload.get("handoffs", ())
    if isinstance(handoffs, str) or not isinstance(handoffs, Sequence):
        raise WorkflowRequestError("workflow pipeline handoffs must be a list")
    if len(handoffs) > MAX_WORKFLOW_HANDOFFS:
        raise WorkflowRequestError(
            f"workflow pipeline declares too many handoffs: "
            f"{len(handoffs)} > {MAX_WORKFLOW_HANDOFFS}"
        )
    metadata = payload.get("metadata", {})
    if metadata in (None, ""):
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise WorkflowRequestError("workflow pipeline metadata must be an object")
    if len(metadata) > MAX_METADATA_ENTRIES:
        raise WorkflowRequestError("workflow pipeline metadata exceeds the entry bound")
    budget = payload.get("action_budget", 0)
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise WorkflowRequestError("workflow pipeline action_budget must be an integer")
    built = WorkflowPipeline(
        workflow_id=_text(payload.get("workflow_id"), "workflow_id", required=True),
        name=_text(payload.get("name"), "name", required=True),
        steps=tuple(step_from_mapping(item) for item in steps),
        handoffs=tuple(handoff_from_mapping(item) for item in handoffs),
        goal=_text(payload.get("goal"), "goal"),
        metadata=dict(metadata),
    )
    if budget:
        from dataclasses import replace

        built = replace(built, action_budget=budget)
    return built


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
def _bind_pipeline(connector: BoundedWorkflowConnector, request: Mapping[str, Any]) -> None:
    """Validate the request's pipeline onto the connector when needed.

    A connector keeps its session across calls, so a workflow is validated
    once. A request that names a *different* workflow is refused rather than
    silently rebinding, which is what keeps one session tied to one workflow.
    """
    declared = request.get("pipeline")
    current = connector.pipeline
    if declared is not None:
        pipeline = pipeline_from_mapping(declared)
        if current is not None and current.workflow_id != pipeline.workflow_id:
            raise WorkflowRequestError(
                "this workflow session is already bound to "
                f"'{current.workflow_id}' and cannot switch to '{pipeline.workflow_id}'"
            )
        # Re-validating the identical definition is idempotent; a *different*
        # definition under the same workflow id is refused by the connector
        # rather than silently ignored, so a later request cannot quietly
        # substitute the workflow a session already carries approvals for.
        connector.validate(pipeline)
        return
    if current is None:
        raise WorkflowRequestError(
            "no workflow has been validated yet; send the pipeline with the request"
        )
    requested_id = _text(request.get("workflow_id"), "workflow_id")
    if requested_id and requested_id != current.workflow_id:
        raise WorkflowRequestError(
            f"request targets workflow '{requested_id}' but this session is bound to "
            f"'{current.workflow_id}'"
        )


def _recipients(value: Any) -> tuple[str, ...]:
    items = _string_tuple(value, "recipients")
    if len(items) > MAX_DRAFT_RECIPIENTS:
        raise WorkflowRequestError(
            f"draft exceeds max recipients: {len(items)} > {MAX_DRAFT_RECIPIENTS}"
        )
    return items


def _coordination_view(connector: BoundedWorkflowConnector, objective: str) -> dict[str, Any]:
    """Read-only coordination proposal derived from declared workflow state.

    This creates nothing. It reports which communication-shaped steps exist,
    whether their prerequisites are verified and whether a human has approved
    them, so a person can decide what to do next.
    """
    pipeline = connector.pipeline
    if pipeline is None:  # pragma: no cover - _bind_pipeline already guarantees this
        raise WorkflowRequestError("no validated workflow to coordinate")
    session = connector.session
    communication_domains = {
        CapabilityDomain.CALENDAR,
        CapabilityDomain.COMMUNICATION,
        CapabilityDomain.EMAIL,
    }
    participants: list[dict[str, Any]] = []
    for step in pipeline.steps:
        if step.domain not in communication_domains:
            continue
        participants.append(
            {
                "step_id": step.step_id,
                "domain": step.domain_value,
                "operation": step.operation,
                "mutating": step.mutating,
                "requires_approval": bool(step.requires_approval),
                "approved": session.is_approved(step.step_id),
                "dependencies_verified": all(
                    session.is_verified(dependency) for dependency in step.depends_on
                ),
            }
        )
    return {
        "operation": "coordinate",
        "workflow_id": pipeline.workflow_id,
        "objective": objective,
        "coordination_steps": participants,
        "ready_steps": [
            item["step_id"]
            for item in participants
            if item["dependencies_verified"] and (item["approved"] or not item["requires_approval"])
        ],
        "blocked_on_approval": [
            item["step_id"]
            for item in participants
            if item["requires_approval"] and not item["approved"]
        ],
        "execution_order": list(connector.execution_order()),
        "budget_remaining": connector.observe_workflow().budget_remaining,
        "sent": False,
        "delivery_state": "draft_only",
        "note": "coordination is read-only; nothing was created, scheduled or sent",
    }


def execute_workflow_operation(
    connector: BoundedWorkflowConnector, request: Mapping[str, Any]
) -> dict[str, Any]:
    """Run one allowlisted workflow operation and return a JSON-safe projection.

    The connector remains the decision-maker. This function only shapes input
    and output; it never grants approval, never widens a budget and never
    reports success the connector did not report.
    """
    if connector is None:
        raise WorkflowRequestError("workflow operations require an injected connector")
    if not isinstance(request, Mapping):
        raise WorkflowRequestError("workflow request must be structured")
    operation = _text(request.get("operation"), "operation", required=True).lower()
    if operation in REFUSED_WORKFLOW_OPERATIONS:
        raise WorkflowRequestError(
            f"refusing workflow operation '{operation}': this layer drafts and never sends"
        )
    if operation not in WORKFLOW_SANDBOX_OPERATIONS:
        raise WorkflowRequestError(
            f"sandbox workflow allowlist does not support operation: {operation}"
        )

    # The approval flag is runtime state appended by the provider after schema
    # validation, so caller arguments can never contain it.
    approved = bool(request.get("approved", False))
    if operation in MUTATING_WORKFLOW_OPERATIONS and not approved:
        raise WorkflowRequestError(
            f"workflow operation '{operation}' requires an explicit approval before it can run"
        )

    _bind_pipeline(connector, request)
    pipeline = connector.pipeline
    assert pipeline is not None  # _bind_pipeline fails closed otherwise

    if operation == "plan":
        observation = connector.observe_workflow()
        return {
            "operation": "plan",
            "workflow_id": pipeline.workflow_id,
            "state": pipeline.state.value,
            "pipeline_digest": pipeline.digest(),
            "execution_order": list(connector.execution_order()),
            "domains": [domain.value for domain in pipeline.domains],
            "cross_domain": pipeline.cross_domain,
            "declared_action_cost": pipeline.declared_action_cost,
            "action_budget": pipeline.action_budget,
            "trust_map": {
                step_id: trust.value for step_id, trust in connector.trust_map().items()
            },
            "approval_required_steps": [
                step.step_id for step in pipeline.steps if step.requires_approval
            ],
            "observation": observation.safe_dict(),
            "backend": connector.backend.name,
            "live": connector.is_live(),
        }

    if operation == "handoff":
        artifact = connector.handoff(
            _text(request.get("source_step"), "source_step", required=True),
            _text(request.get("target_step"), "target_step", required=True),
            _text(request.get("artifact_key"), "artifact_key", required=True),
        )
        return {
            "operation": "handoff",
            "workflow_id": pipeline.workflow_id,
            "artifact": artifact.safe_dict(),
            "trust": artifact.trust.value,
            "handoff_digests": dict(connector.session.handoff_digests),
            "injection_signals": list(connector.injection_signals),
            "budget_used": connector.session.action_budget.used,
        }

    if operation == "execute":
        step_id = _text(request.get("step_id"), "step_id", required=True)
        approver = _text(request.get("approver"), "approver")
        if approver:
            connector.grant_approval(step_id, approver=approver)
        execution = connector.execute_step(step_id)
        return {
            "operation": "execute",
            "workflow_id": pipeline.workflow_id,
            "step": execution.safe_dict(),
            "verified": execution.verified,
            "status": execution.status.value,
            "observation": connector.observe_workflow().safe_dict(),
            "budget_used": connector.session.action_budget.used,
        }

    if operation == "coordinate":
        return _coordination_view(connector, _text(request.get("objective"), "objective"))

    if operation == "draft":
        step_id = _text(request.get("step_id"), "step_id", required=True)
        approver = _text(request.get("approver"), "approver")
        if approver:
            connector.grant_approval(step_id, approver=approver)
        draft = connector.create_draft(
            step_id,
            channel=_text(request.get("channel", "email"), "channel") or "email",
            recipients=_recipients(request.get("recipients")),
            subject=_text(request.get("subject"), "subject"),
            body=_text(request.get("body"), "body"),
        )
        return {
            "operation": "draft",
            "workflow_id": pipeline.workflow_id,
            "draft": draft.safe_dict(),
            "sent": False,
            "delivery_state": draft.delivery_state,
            "budget_used": connector.session.action_budget.used,
        }

    if operation == "observe_step":
        step_id = _text(request.get("step_id"), "step_id", required=True)
        state = connector.observe_step(step_id)
        return {
            "operation": "observe_step",
            "workflow_id": pipeline.workflow_id,
            "step_state": redact_structure(state),
        }

    if operation == "observe_workflow":
        return {
            "operation": "observe_workflow",
            "workflow_id": pipeline.workflow_id,
            "observation": connector.observe_workflow().safe_dict(),
        }

    # operation == "verify"
    observation = connector.verify_workflow()
    return {
        "operation": "verify",
        "workflow_id": pipeline.workflow_id,
        "observation": observation.safe_dict(),
        "complete": observation.complete,
        "verified_steps": list(observation.verified_steps),
    }


__all__ = [
    "MUTATING_WORKFLOW_OPERATIONS",
    "REFUSED_WORKFLOW_OPERATIONS",
    "WORKFLOW_SANDBOX_OPERATIONS",
    "WorkflowRequestError",
    "execute_workflow_operation",
    "handoff_from_mapping",
    "pipeline_from_mapping",
    "step_from_mapping",
]
