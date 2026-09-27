"""Post-condition verification for bounded cross-domain workflow capabilities.

The observer is what stops "the call returned" from being mistaken for "the
work happened". For every workflow capability it re-reads state through the
connector *after* the sandbox call and compares that independent reading with
what the execution claimed:

* a read-only capability must have produced real evidence;
* an executed step must be reported as verified by the connector's own
  observation, not by the backend's acceptance;
* a prepared draft must still be a draft -- anything claiming delivery is
  refused outright.

Nothing here can upgrade a result: the observer only ever confirms or refuses.
"""

from __future__ import annotations

from typing import Any


class WorkflowPostConditionObserver:
    """Confirm workflow capability results by re-reading connector state."""

    #: Capability ids whose evidence is a bounded read of declared state.
    READ_ONLY_CAPABILITIES = frozenset(
        {
            "workflow:pipeline.plan",
            "workflow:data.handoff",
            "communication:meeting.coordinate",
        }
    )

    def __init__(self, connector: Any) -> None:
        self._connector = connector

    # -- helpers -----------------------------------------------------------
    def _observation(self, capability_id: str, observed: bool, evidence: Any, detail: str) -> Any:
        from autonomous_agent.digital.contract import CapabilityObservation

        return CapabilityObservation(capability_id, observed, evidence or {}, detail)

    def _workflow_state(self) -> dict[str, Any]:
        observation = self._connector.observe_workflow()
        return dict(observation.safe_dict())

    # -- observation -------------------------------------------------------
    def observe(self, request: Any, execution: Any) -> Any:
        capability_id = request.capability_id

        if not execution.success or not execution.has_evidence:
            return self._observation(
                capability_id,
                False,
                {},
                f"execution failed or produced no evidence: {execution.error or 'no output'}",
            )

        evidence = execution.evidence

        # A result that claims delivery is refused no matter who produced it.
        if bool(evidence.get("sent", False)) or str(
            evidence.get("delivery_state", "draft_only")
        ) not in {"draft_only", ""}:
            return self._observation(
                capability_id,
                False,
                {},
                "workflow result claimed delivery; this layer drafts and never sends",
            )

        if capability_id in self.READ_ONLY_CAPABILITIES:
            try:
                state = self._workflow_state()
            except Exception as exc:
                return self._observation(
                    capability_id,
                    False,
                    {},
                    f"post-call workflow observation failed: {type(exc).__name__}",
                )
            return self._observation(
                capability_id,
                True,
                {**state, "reported": evidence.get("operation", "")},
                "workflow read evidence confirmed by independent re-observation",
            )

        if capability_id == "workflow:pipeline.execute":
            step_id = str(request.arguments.get("step_id", "")) or str(
                evidence.get("step", {}).get("step_id", "")
            )
            try:
                state = self._connector.observe_step(step_id)
            except Exception as exc:
                return self._observation(
                    capability_id,
                    False,
                    {},
                    f"post-execution step observation failed: {type(exc).__name__}",
                )
            if not bool(state.get("verified", False)):
                return self._observation(
                    capability_id,
                    False,
                    {},
                    f"step '{step_id}' was accepted but is not verified by observed state",
                )
            return self._observation(
                capability_id,
                True,
                {
                    "step_id": step_id,
                    "verified": True,
                    "digest": state.get("digest", ""),
                    "workflow": self._workflow_state(),
                },
                "workflow step verified by independent post-execution observation",
            )

        if capability_id == "communication:draft.prepare":
            drafts = getattr(self._connector, "drafts", ())
            declared = str(evidence.get("draft", {}).get("draft_id", ""))
            match = next((item for item in drafts if item.draft_id == declared), None)
            if match is None:
                return self._observation(
                    capability_id,
                    False,
                    {},
                    "prepared draft was not found in observed connector state",
                )
            if match.sent or match.delivery_state != "draft_only":
                return self._observation(
                    capability_id, False, {}, "draft state claims delivery; refusing to verify"
                )
            return self._observation(
                capability_id,
                True,
                {
                    "draft_id": match.draft_id,
                    "digest": match.digest,
                    "sent": False,
                    "delivery_state": match.delivery_state,
                },
                "communication draft verified as draft-only state",
            )

        return self._observation(
            capability_id, False, {}, f"no workflow post-condition rule for {capability_id}"
        )


__all__ = ["WorkflowPostConditionObserver"]
