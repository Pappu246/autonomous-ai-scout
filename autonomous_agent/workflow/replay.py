"""Composite replay protection for bounded cross-domain workflows.

A cross-domain workflow can be suspended and resumed, so "did this already
happen?" has to be answerable without re-running anything. This protector
answers it with deterministic SHA-256 identities over the *declaration plus
inputs* of each unit of work:

* a step identity binds workflow, step, capability, operation and an inputs
  digest (parameters plus the digests of every inbound artifact);
* a handoff identity binds workflow, source step, target step, artifact key
  and the payload digest;
* a mutation identity additionally binds the declared effect, so a read that
  later becomes a write is treated as a different action rather than a repeat;
* an artifact identity binds workflow, producing step, key and content digest.

Session ids are deliberately *excluded*: the whole point is that a resume in a
new session still recognizes finished work. Two structurally identical steps in
different workflows stay distinct because the workflow id is always included.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from .models import (
    StepEffect,
    WorkflowArtifact,
    WorkflowHandoff,
    WorkflowReplayError,
    WorkflowStep,
    redact_structure,
)


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def inbound_digest(inbound: Mapping[str, WorkflowArtifact] | None) -> str:
    """Deterministic digest of the artifacts flowing into a step."""
    items = {
        str(key): (value.sha256 or _digest({"payload": redact_structure(value.payload)}))
        for key, value in sorted((inbound or {}).items())
    }
    return _digest({"inbound": items})


class WorkflowReplayProtector:
    """Prevents duplicate execution of completed steps, handoffs and mutations."""

    def __init__(self, completed: Iterable[str] = ()) -> None:
        self._completed: set[str] = {str(item) for item in completed}

    # -- identities --------------------------------------------------------
    def step_key(
        self,
        *,
        workflow_id: str,
        step: WorkflowStep,
        inbound: Mapping[str, WorkflowArtifact] | None = None,
    ) -> str:
        """Identity of one step execution: workflow + step + capability + inputs."""
        if not isinstance(step, WorkflowStep):
            raise WorkflowReplayError("a WorkflowStep is required to compute a replay identity")
        return _digest(
            {
                "kind": "step",
                "workflow_id": str(workflow_id),
                "step_id": step.step_id,
                "capability_id": step.capability_id,
                "operation": step.operation,
                "effect": step.effect.value if isinstance(step.effect, StepEffect) else str(step.effect),
                "parameters": redact_structure(dict(step.parameters)),
                "inbound": inbound_digest(inbound),
            }
        )

    def mutation_key(
        self,
        *,
        workflow_id: str,
        step: WorkflowStep,
        inbound: Mapping[str, WorkflowArtifact] | None = None,
    ) -> str:
        """Identity of one *mutating* step execution."""
        if not step.mutating:
            raise WorkflowReplayError(
                f"step '{step.step_id}' is not mutating; use step_key for read-only work"
            )
        return _digest(
            {
                "kind": "mutation",
                "workflow_id": str(workflow_id),
                "step_id": step.step_id,
                "capability_id": step.capability_id,
                "operation": step.operation,
                "parameters": redact_structure(dict(step.parameters)),
                "inbound": inbound_digest(inbound),
            }
        )

    def handoff_key(
        self,
        *,
        workflow_id: str,
        handoff: WorkflowHandoff,
        payload_digest: str = "",
    ) -> str:
        """Identity of one cross-domain artifact transfer."""
        if not isinstance(handoff, WorkflowHandoff):
            raise WorkflowReplayError("a WorkflowHandoff is required to compute a replay identity")
        return _digest(
            {
                "kind": "handoff",
                "workflow_id": str(workflow_id),
                "source_step": handoff.source_step,
                "target_step": handoff.target_step,
                "artifact_key": handoff.artifact_key,
                "trust": handoff.trust.value if hasattr(handoff.trust, "value") else str(handoff.trust),
                "payload_digest": str(payload_digest),
            }
        )

    def artifact_key_digest(
        self,
        *,
        workflow_id: str,
        artifact: WorkflowArtifact,
    ) -> str:
        """Identity of one verified artifact."""
        if not isinstance(artifact, WorkflowArtifact):
            raise WorkflowReplayError("a WorkflowArtifact is required to compute a replay identity")
        return _digest(
            {
                "kind": "artifact",
                "workflow_id": str(workflow_id),
                "source_step": artifact.source_step,
                "artifact_key": artifact.artifact_key,
                "sha256": artifact.sha256 or _digest({"payload": redact_structure(artifact.payload)}),
            }
        )

    # -- bookkeeping -------------------------------------------------------
    def already_completed(self, key: str) -> bool:
        return str(key) in self._completed

    def check(self, key: str, detail: str = "") -> None:
        """Raise if this unit of work already completed before the resume."""
        if str(key) in self._completed:
            suffix = f": {detail}" if detail else ""
            raise WorkflowReplayError(
                f"refusing to repeat cross-domain work that already completed before resume{suffix}"
            )

    def record(self, key: str) -> None:
        self._completed.add(str(key))

    def record_all(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.record(key)

    def completed_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._completed))

    def reset(self) -> None:
        self._completed.clear()

    @classmethod
    def from_keys(cls, keys: Sequence[str]) -> "WorkflowReplayProtector":
        """Rebuild replay state from a session snapshot's digests."""
        return cls(keys)


__all__ = ["WorkflowReplayProtector", "inbound_digest"]
