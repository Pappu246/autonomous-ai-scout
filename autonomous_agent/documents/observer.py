"""Post-condition verification and state observation for document processing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autonomous_agent.digital.contract import (
        CapabilityExecution,
        CapabilityObservation,
        CapabilityRequest,
    )


class DocumentsPostConditionObserver:
    """Verifies that executed document operations produced real, observable changes."""

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

        # 1. Read-only inspection / extraction tools
        if cap_id in {
            "documents:inspect",
            "documents:extract.text",
            "documents:extract.tables",
            "documents:page.read",
        }:
            return CapabilityObservation(
                cap_id,
                True,
                execution.evidence,
                detail="document read evidence collected and verified",
            )

        # 2. Mutating transform operation
        if cap_id == "documents:transform":
            output_path = request.arguments.get("output_path") or execution.evidence.get("output_path")
            if not output_path:
                return CapabilityObservation(
                    cap_id,
                    False,
                    detail="transform post-condition verification failed: no output_path in request or evidence",
                )
            try:
                meta = self._connector.inspect(str(output_path))
            except Exception as exc:
                return CapabilityObservation(
                    cap_id,
                    False,
                    detail=f"post-transform inspect failed: {exc}",
                )
            evidence_sha = str(execution.evidence.get("sha256", ""))
            if evidence_sha and meta.sha256 and meta.sha256 != evidence_sha:
                return CapabilityObservation(
                    cap_id,
                    False,
                    {"expected_sha256": evidence_sha, "actual_sha256": meta.sha256},
                    detail="transformed document checksum mismatch on re-inspection",
                )
            return CapabilityObservation(
                cap_id,
                True,
                meta.safe_dict(),
                detail="document transform confirmed by independent file inspection",
            )

        return CapabilityObservation(cap_id, execution.has_evidence, execution.evidence)


__all__ = ["DocumentsPostConditionObserver"]
