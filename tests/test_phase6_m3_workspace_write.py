"""Phase 6 M3 tests for one approval-gated, preconditioned real write."""
from __future__ import annotations

from pathlib import Path

import pytest

from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.filesystem_workspace import WorkspaceConnector
from autonomous_agent.workflow import (
    BoundedWorkflowConnector,
    ControlledRealWorkflowBackend,
    StepEffect,
    VerificationStatus,
    WorkflowPipeline,
    WorkflowReplayError,
    WorkflowStep,
    WorkspaceRealWorkflowWriteAdapter,
    artifact_digest,
)
from autonomous_agent.workflow.models import WorkflowApprovalError


def _fingerprint(workspace: WorkspaceConnector, relative_path: str) -> str:
    return workspace.read(relative_path).fingerprint.lower()


def _pipeline(path: str, content: str, fingerprint: str) -> WorkflowPipeline:
    return WorkflowPipeline(
        workflow_id="wf-m3-workspace-write",
        name="M3 workspace write",
        steps=(
            WorkflowStep(
                step_id="write",
                domain=CapabilityDomain.FILESYSTEM,
                capability_id="filesystem:write",
                operation="write",
                parameters={
                    "path": path,
                    "content": content,
                    "precondition": {"fingerprint": fingerprint},
                },
                produces=("result",),
                effect=StepEffect.MUTATING,
                requires_approval=True,
            ),
        ),
        action_budget=5,
    )


def _connector(tmp_path: Path, *, content: str = "updated") -> tuple[BoundedWorkflowConnector, str]:
    source = tmp_path / "notes.txt"
    source.write_text("before", encoding="utf-8")
    workspace = WorkspaceConnector(tmp_path)
    fingerprint = _fingerprint(workspace, "notes.txt")
    connector = BoundedWorkflowConnector(
        backend=ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace)),
        known_capability_ids=("filesystem:write",),
    )
    connector.validate(_pipeline("notes.txt", content, fingerprint))
    return connector, fingerprint


def test_m3_write_requires_human_approval(tmp_path: Path):
    connector, _ = _connector(tmp_path)
    with pytest.raises(WorkflowApprovalError):
        connector.execute_step("write")
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "before"


def test_m3_write_is_preconditioned_and_independently_verified(tmp_path: Path):
    connector, _ = _connector(tmp_path, content="after")
    connector.grant_approval("write", approver="human-reviewer")
    execution = connector.execute_step("write")
    assert execution.status is VerificationStatus.VERIFIED
    assert execution.accepted is True
    assert execution.observed is True
    assert execution.verified is True
    assert execution.evidence["execution_envelope"]["precondition_digest"]
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "after"


def test_m3_stale_precondition_fails_closed_without_writing(tmp_path: Path):
    connector, fingerprint = _connector(tmp_path, content="should-not-land")
    (tmp_path / "notes.txt").write_text("changed-outside-workflow", encoding="utf-8")
    connector.grant_approval("write", approver="human-reviewer")
    execution = connector.execute_step("write")
    assert execution.status is VerificationStatus.FAILED
    assert execution.accepted is False
    assert execution.verified is False
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "changed-outside-workflow"
    assert fingerprint != _fingerprint(WorkspaceConnector(tmp_path), "notes.txt")


def test_m3_precondition_changes_idempotency_identity():
    from autonomous_agent.workflow.real_backend import ExecutionEnvelope, RealNetworkPolicy

    one = WorkflowStep(
        step_id="write",
        domain=CapabilityDomain.FILESYSTEM,
        capability_id="filesystem:write",
        operation="write",
        parameters={"path": "notes.txt", "content": "a", "precondition": {"fingerprint": "a" * 64}},
        produces=("result",),
        effect=StepEffect.MUTATING,
        requires_approval=True,
    )
    two = WorkflowStep(
        step_id="write",
        domain=CapabilityDomain.FILESYSTEM,
        capability_id="filesystem:write",
        operation="write",
        parameters={"path": "notes.txt", "content": "a", "precondition": {"fingerprint": "b" * 64}},
        produces=("result",),
        effect=StepEffect.MUTATING,
        requires_approval=True,
    )
    left = ExecutionEnvelope.build(
        workflow_id="wf-m3",
        step=one,
        provider="workspace-write",
        network_policy=RealNetworkPolicy.NONE,
        required_scopes=("workspace.write",),
        approved=True,
        precondition_digest=artifact_digest({"fingerprint": "a" * 64}),
    )
    right = ExecutionEnvelope.build(
        workflow_id="wf-m3",
        step=two,
        provider="workspace-write",
        network_policy=RealNetworkPolicy.NONE,
        required_scopes=("workspace.write",),
        approved=True,
        precondition_digest=artifact_digest({"fingerprint": "b" * 64}),
    )
    assert left.precondition_digest != right.precondition_digest
    assert left.idempotency_key != right.idempotency_key


class AmbiguousWriteAdapter(WorkspaceRealWorkflowWriteAdapter):
    """Test adapter that writes successfully but makes post-write verification uncertain."""

    def __init__(self, workspace: WorkspaceConnector):
        super().__init__(workspace)
        self.execute_count = 0

    def execute(self, envelope, step, inbound):
        result = super().execute(envelope, step, inbound)
        self.execute_count += 1
        return result

    def observe(self, envelope):
        observed = super().observe(envelope)
        if observed.observed:
            return type(observed)(
                observed=False,
                evidence=dict(observed.evidence),
                detail="simulated ambiguous provider outcome; postcondition not trusted",
            )
        return observed


def test_m3_ambiguous_mutation_is_not_blindly_retried(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("before", encoding="utf-8")
    workspace = WorkspaceConnector(tmp_path)
    fingerprint = _fingerprint(workspace, "notes.txt")
    adapter = AmbiguousWriteAdapter(workspace)
    connector = BoundedWorkflowConnector(
        backend=ControlledRealWorkflowBackend(adapter),
        known_capability_ids=("filesystem:write",),
    )
    connector.validate(_pipeline("notes.txt", "after", fingerprint))
    connector.grant_approval("write", approver="human-reviewer")
    first = connector.execute_step("write")
    assert first.accepted is True
    assert first.observed is True
    assert first.verified is False
    assert adapter.execute_count == 1
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "after"
    with pytest.raises(WorkflowReplayError):
        connector.execute_step("write")
    assert adapter.execute_count == 1


def test_m3_write_rejects_secret_like_content_at_workspace_boundary(tmp_path: Path):
    connector, _ = _connector(tmp_path, content="api_key=not-allowed")
    connector.grant_approval("write", approver="human-reviewer")
    execution = connector.execute_step("write")
    assert execution.status is VerificationStatus.FAILED
    assert execution.accepted is False
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "before"
