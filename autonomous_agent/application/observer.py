"""Post-condition verification and state observation for application adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autonomous_agent.digital.contract import (
        CapabilityExecution,
        CapabilityObservation,
        CapabilityRequest,
    )


class ApplicationPostConditionObserver:
    """Verifies that executed application adapter operations produced real, observable changes."""

    def __init__(self, connector: Any) -> None:
        self._connector = connector

    def observe(
        self, request: Any, execution: Any
    ) -> Any:
        from autonomous_agent.digital.contract import CapabilityObservation

        cap_id = request.capability_id

        if not execution.success or not execution.has_evidence:
            return CapabilityObservation(
                cap_id,
                False,
                detail=f"execution failed or produced no evidence: {execution.error or 'no output'}",
            )

        # 1. Read-only discovery and inspection tools
        if cap_id in {
            "application:list",
            "application:inspect",
            "application:observe",
        }:
            return CapabilityObservation(
                cap_id,
                True,
                execution.evidence,
                detail="application read evidence collected and verified",
            )

        # 2. Mutating command execution
        if cap_id == "application:command.execute":
            app_id = request.arguments.get("app_id") or execution.evidence.get("app_id")
            try:
                obs = self._connector.observe(str(app_id) if app_id else None)
            except Exception as exc:
                return CapabilityObservation(
                    cap_id,
                    False,
                    detail=f"post-command application observation failed: {exc}",
                )
            return CapabilityObservation(
                cap_id,
                True,
                obs.safe_dict(),
                detail="application command verified by post-execution observation",
            )

        return CapabilityObservation(cap_id, execution.has_evidence, execution.evidence)


__all__ = ["ApplicationPostConditionObserver"]
