"""Semantic target resolution for the bounded cross-domain workflow domain.

Targets name *semantic* things in a validated workflow:

* a declared step,
* a declared artifact,
* a declared handoff edge,
* a generated communication draft,
* a domain output recorded for a completed step.

They are bound to the workflow id and to the session epoch that produced them,
so a target resolved against one workflow or one epoch can never be replayed
against another. Resolution is deterministic and fails closed: there is no
coordinate fallback, no "nearest match", no positional index and no guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import (
    MAX_ARTIFACT_KEY_LENGTH,
    MAX_STEP_ID_LENGTH,
    TargetResolutionError,
    WorkflowPipeline,
    WorkflowStep,
    redact_secret,
)
from .policy import validate_artifact_key, validate_step_id


#: Target kinds this resolver understands. Anything else fails closed.
TARGET_TYPES: frozenset[str] = frozenset({"step", "artifact", "handoff", "draft", "domain_output"})

#: Coordinate-like or positional references are rejected outright: a workflow
#: is addressed semantically, never by screen position or ordinal index.
_POSITIONAL = re.compile(r"^\s*[\[\(<@#]?\s*-?\d+(?:\.\d+)?\s*(?:[,x:;]\s*-?\d+(?:\.\d+)?\s*)+[\]\)>]?\s*$")
_BARE_ORDINAL = re.compile(r"^\s*[\[\(<@#]?\s*-?\d+(?:\.\d+)?\s*[\]\)>]?\s*$")


def reject_positional_reference(value: Any, label: str = "target reference") -> str:
    """Fail closed on coordinate-like, ordinal or non-string target references."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise TargetResolutionError(
            f"{label} must be a semantic identifier, not a positional index: {value!r}"
        )
    if isinstance(value, (list, tuple)):
        raise TargetResolutionError(
            f"{label} must be a semantic identifier, not a coordinate pair: {value!r}"
        )
    if not isinstance(value, str):
        raise TargetResolutionError(f"{label} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise TargetResolutionError(f"{label} cannot be empty")
    if _POSITIONAL.match(cleaned) or _BARE_ORDINAL.match(cleaned):
        raise TargetResolutionError(
            f"{label} must be a semantic identifier, not a coordinate or index: {value!r}"
        )
    return cleaned


@dataclass(frozen=True)
class WorkflowTarget:
    """A resolved semantic target bound to one workflow and one epoch."""

    target_id: str
    workflow_id: str
    target_type: str
    step_id: str = ""
    artifact_key: str = ""
    peer_step_id: str = ""
    domain: str = ""
    epoch: int = 0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id[:MAX_STEP_ID_LENGTH + MAX_ARTIFACT_KEY_LENGTH + 8],
            "workflow_id": self.workflow_id,
            "target_type": self.target_type,
            "step_id": self.step_id,
            "artifact_key": self.artifact_key,
            "peer_step_id": self.peer_step_id,
            "domain": self.domain,
            "epoch": self.epoch,
        }


class WorkflowTargetResolver:
    """Resolve and validate semantic workflow targets, failing closed."""

    # -- resolution --------------------------------------------------------
    def resolve_step(self, pipeline: WorkflowPipeline, step_id: Any, *, epoch: int = 0) -> WorkflowTarget:
        step = self._require_step(pipeline, step_id)
        return WorkflowTarget(
            target_id=f"step:{step.step_id}",
            workflow_id=pipeline.workflow_id,
            target_type="step",
            step_id=step.step_id,
            domain=step.domain_value,
            epoch=epoch,
        )

    def resolve_artifact(
        self,
        pipeline: WorkflowPipeline,
        artifact_key: Any,
        *,
        producer_step: Any = "",
        epoch: int = 0,
    ) -> WorkflowTarget:
        cleaned_key = validate_artifact_key(reject_positional_reference(artifact_key, "artifact key"))

        producers = [step for step in pipeline.steps if cleaned_key in step.produces]
        if producer_step:
            step = self._require_step(pipeline, producer_step)
            if cleaned_key not in step.produces:
                raise TargetResolutionError(
                    f"step '{step.step_id}' does not produce artifact '{cleaned_key}'"
                )
            producers = [step]
        if not producers:
            raise TargetResolutionError(
                f"artifact '{cleaned_key}' is not produced by any step in workflow '{pipeline.workflow_id}'"
            )
        if len(producers) > 1:
            raise TargetResolutionError(
                f"artifact '{cleaned_key}' is ambiguous: produced by "
                f"{', '.join(sorted(item.step_id for item in producers))}"
            )
        producer = producers[0]
        return WorkflowTarget(
            target_id=f"artifact:{producer.step_id}:{cleaned_key}",
            workflow_id=pipeline.workflow_id,
            target_type="artifact",
            step_id=producer.step_id,
            artifact_key=cleaned_key,
            domain=producer.domain_value,
            epoch=epoch,
        )

    def resolve_handoff(
        self,
        pipeline: WorkflowPipeline,
        source_step: Any,
        target_step: Any,
        artifact_key: Any,
        *,
        epoch: int = 0,
    ) -> WorkflowTarget:
        source = self._require_step(pipeline, source_step)
        destination = self._require_step(pipeline, target_step)
        cleaned_key = validate_artifact_key(reject_positional_reference(artifact_key, "artifact key"))

        for handoff in pipeline.handoffs:
            if (
                handoff.source_step == source.step_id
                and handoff.target_step == destination.step_id
                and handoff.artifact_key == cleaned_key
            ):
                return WorkflowTarget(
                    target_id=f"handoff:{source.step_id}->{destination.step_id}:{cleaned_key}",
                    workflow_id=pipeline.workflow_id,
                    target_type="handoff",
                    step_id=destination.step_id,
                    artifact_key=cleaned_key,
                    peer_step_id=source.step_id,
                    domain=destination.domain_value,
                    epoch=epoch,
                )
        raise TargetResolutionError(
            f"no declared handoff '{cleaned_key}' from '{source.step_id}' to '{destination.step_id}'"
        )

    def resolve_draft(self, pipeline: WorkflowPipeline, step_id: Any, *, epoch: int = 0) -> WorkflowTarget:
        step = self._require_step(pipeline, step_id)
        return WorkflowTarget(
            target_id=f"draft:{step.step_id}",
            workflow_id=pipeline.workflow_id,
            target_type="draft",
            step_id=step.step_id,
            domain=step.domain_value,
            epoch=epoch,
        )

    def resolve_domain_output(
        self,
        pipeline: WorkflowPipeline,
        step_id: Any,
        *,
        epoch: int = 0,
    ) -> WorkflowTarget:
        step = self._require_step(pipeline, step_id)
        return WorkflowTarget(
            target_id=f"domain_output:{step.domain_value}:{step.step_id}",
            workflow_id=pipeline.workflow_id,
            target_type="domain_output",
            step_id=step.step_id,
            domain=step.domain_value,
            epoch=epoch,
        )

    # -- validation --------------------------------------------------------
    def ensure_current(
        self,
        target: WorkflowTarget,
        pipeline: WorkflowPipeline,
        *,
        epoch: int = 0,
    ) -> WorkflowStep:
        """Verify a target still belongs to this workflow and this epoch."""
        if not isinstance(target, WorkflowTarget):
            raise TargetResolutionError("a WorkflowTarget is required")
        if not isinstance(pipeline, WorkflowPipeline):
            raise TargetResolutionError("a WorkflowPipeline is required")
        if target.target_type not in TARGET_TYPES:
            raise TargetResolutionError(f"unsupported target type: {target.target_type}")
        if target.workflow_id != pipeline.workflow_id:
            raise TargetResolutionError(
                f"target belongs to a foreign workflow: {redact_secret(target.workflow_id)} "
                f"!= {pipeline.workflow_id}"
            )
        if target.epoch != epoch:
            raise TargetResolutionError(
                "target is stale: it was resolved against a different session epoch"
            )
        step = self._require_step(pipeline, target.step_id)
        if target.artifact_key:
            if target.target_type == "artifact" and target.artifact_key not in step.produces:
                raise TargetResolutionError(
                    f"artifact '{target.artifact_key}' is no longer produced by '{step.step_id}'"
                )
            if target.target_type == "handoff" and target.artifact_key not in step.consumes:
                raise TargetResolutionError(
                    f"artifact '{target.artifact_key}' is no longer consumed by '{step.step_id}'"
                )
        return step

    # -- internals ---------------------------------------------------------
    def _require_step(self, pipeline: WorkflowPipeline, step_id: Any) -> WorkflowStep:
        if not isinstance(pipeline, WorkflowPipeline):
            raise TargetResolutionError("a WorkflowPipeline is required")
        cleaned = reject_positional_reference(step_id, "step id")
        try:
            resolved = validate_step_id(cleaned)
        except Exception as exc:  # fail closed on any malformed identifier
            raise TargetResolutionError(f"invalid step reference: {step_id!r}") from exc
        for step in pipeline.steps:
            if step.step_id == resolved:
                return step
        raise TargetResolutionError(
            f"step '{resolved}' is not declared in workflow '{pipeline.workflow_id}'"
        )


__all__ = ["TARGET_TYPES", "WorkflowTarget", "WorkflowTargetResolver", "reject_positional_reference"]
