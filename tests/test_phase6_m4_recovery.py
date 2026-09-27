"""Phase 6 M4 durable recovery and idempotency hardening tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.filesystem_workspace import WorkspaceConnector
from autonomous_agent.workflow import (
    ActionBudget,
    BoundedWorkflowConnector,
    ControlledRealWorkflowBackend,
    StepEffect,
    VerificationStatus,
    WorkflowPipeline,
    WorkflowReplayError,
    WorkflowSession,
    WorkflowReplayProtector,
    WorkflowStep,
    WorkspaceRealWorkflowWriteAdapter,
)
from autonomous_agent.workflow.real_backend import RealBackendContractError


def _fingerprint(workspace: WorkspaceConnector, path: str) -> str:
    return workspace.read(path).fingerprint.lower()


def _pipeline(path: str, content: str, fingerprint: str) -> WorkflowPipeline:
    return WorkflowPipeline(
        workflow_id="wf-m4-recovery",
        name="M4 durable recovery",
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


def test_m4_checkpoint_is_secret_free_and_rehydratable(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("before", encoding="utf-8")
    workspace = WorkspaceConnector(tmp_path)
    fp = _fingerprint(workspace, "notes.txt")
    backend = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    connector = BoundedWorkflowConnector(
        backend=backend,
        known_capability_ids=("filesystem:write",),
    )
    connector.validate(_pipeline("notes.txt", "after", fp))
    connector.grant_approval("write", approver="human")
    execution = connector.execute_step("write")
    assert execution.verified is True

    checkpoint = backend.checkpoint()
    encoded = str(checkpoint)
    assert "after" not in encoded
    assert "api_key" not in encoded.lower()
    assert checkpoint["version"] == 1

    restored = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    restored.restore_checkpoint(checkpoint)
    recovered = restored.recover_step("write")
    assert recovered["recovery"] == "observed"
    assert recovered["observed"] is True
    assert recovered["artifact_digests"]


def test_m4_restored_backend_refuses_duplicate_execution(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("before", encoding="utf-8")
    workspace = WorkspaceConnector(tmp_path)
    fp = _fingerprint(workspace, "notes.txt")
    pipeline = _pipeline("notes.txt", "after", fp)
    backend = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    connector = BoundedWorkflowConnector(
        backend=backend,
        known_capability_ids=("filesystem:write",),
    )
    connector.validate(pipeline)
    connector.grant_approval("write", approver="human")
    connector.execute_step("write")

    checkpoint = backend.checkpoint()
    restored = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    restored.restore_checkpoint(checkpoint)

    with pytest.raises(WorkflowReplayError):
        restored.execute_step(pipeline.step("write"), workflow_id=pipeline.workflow_id, approved=True)
    assert source.read_text(encoding="utf-8") == "after"


def test_m4_tampered_checkpoint_identity_fails_closed(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("before", encoding="utf-8")
    workspace = WorkspaceConnector(tmp_path)
    fp = _fingerprint(workspace, "notes.txt")
    backend = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    connector = BoundedWorkflowConnector(
        backend=backend,
        known_capability_ids=("filesystem:write",),
    )
    connector.validate(_pipeline("notes.txt", "after", fp))
    connector.grant_approval("write", approver="human")
    connector.execute_step("write")

    checkpoint = backend.checkpoint()
    checkpoint["envelopes"][0]["idempotency_key"] = "0" * 64
    restored = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    with pytest.raises(RealBackendContractError):
        restored.restore_checkpoint(checkpoint)


def test_m4_mutation_identity_survives_session_checkpoint():
    session = WorkflowSession(action_budget=ActionBudget(limit=5))
    session.bind("wf-m4-session")
    digest = "d" * 64
    session.record_mutation(digest)
    snapshot = session.snapshot()

    assert digest in snapshot.mutation_digests
    restored = WorkflowSession.restore(snapshot)
    replay = WorkflowReplayProtector.from_keys(restored.snapshot().replay_keys())
    with pytest.raises(WorkflowReplayError):
        replay.check(digest, "mutation after checkpoint")


def test_m4_recovery_context_does_not_store_raw_mutation_content(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("before", encoding="utf-8")
    workspace = WorkspaceConnector(tmp_path)
    fp = _fingerprint(workspace, "notes.txt")
    backend = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    connector = BoundedWorkflowConnector(
        backend=backend,
        known_capability_ids=("filesystem:write",),
    )
    connector.validate(_pipeline("notes.txt", "after", fp))
    connector.grant_approval("write", approver="human")
    connector.execute_step("write")

    checkpoint = backend.checkpoint()
    context = checkpoint["envelopes"][0]["recovery_context"]
    assert context["path"] == "notes.txt"
    assert len(context["content_digest"]) == 64
    assert "after" not in str(context)


def test_m4_fresh_adapter_recovers_actual_postcondition(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("before", encoding="utf-8")
    workspace = WorkspaceConnector(tmp_path)
    fp = _fingerprint(workspace, "notes.txt")
    backend = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    connector = BoundedWorkflowConnector(
        backend=backend,
        known_capability_ids=("filesystem:write",),
    )
    connector.validate(_pipeline("notes.txt", "after", fp))
    connector.grant_approval("write", approver="human")
    connector.execute_step("write")

    checkpoint = backend.checkpoint()
    fresh = ControlledRealWorkflowBackend(WorkspaceRealWorkflowWriteAdapter(workspace))
    fresh.restore_checkpoint(checkpoint)
    recovered = fresh.recover_step("write")

    assert recovered["recovery"] == "observed"
    assert recovered["observed"] is True
    assert source.read_text(encoding="utf-8") == "after"
    with pytest.raises(WorkflowReplayError):
        fresh.execute_step(
            connector.pipeline.step("write"),
            workflow_id="wf-m4-recovery",
            approved=True,
        )
