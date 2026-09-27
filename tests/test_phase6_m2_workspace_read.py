"""M2 end-to-end tests for the bounded real workspace read vertical slice."""

from __future__ import annotations

from pathlib import Path

from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.filesystem_workspace import WorkspaceConnector
from autonomous_agent.workflow import (
    BoundedWorkflowConnector,
    StepEffect,
    VerificationStatus,
    WorkflowPipeline,
    WorkflowSecurityError,
    WorkflowStep,
    WorkspaceRealWorkflowAdapter,
)


def _pipeline(path: str) -> WorkflowPipeline:
    return WorkflowPipeline(
        workflow_id="wf-m2-workspace-read",
        name="M2 workspace read",
        steps=(
            WorkflowStep(
                step_id="read",
                domain=CapabilityDomain.FILESYSTEM,
                capability_id="filesystem:read",
                operation="read",
                parameters={"path": path},
                produces=("result",),
                effect=StepEffect.READ_ONLY,
                requires_approval=False,
            ),
        ),
        action_budget=5,
    )


def test_m2_real_workspace_read_is_independently_verified(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("hello from the real workspace", encoding="utf-8")

    workspace = WorkspaceConnector(tmp_path)
    adapter = WorkspaceRealWorkflowAdapter(workspace)
    connector = BoundedWorkflowConnector(
        backend=__import__(
            "autonomous_agent.workflow.real_backend",
            fromlist=["ControlledRealWorkflowBackend"],
        ).ControlledRealWorkflowBackend(adapter),
        known_capability_ids=("filesystem:read",),
    )

    connector.validate(_pipeline("notes.txt"))
    execution = connector.execute_step("read")

    assert execution.status is VerificationStatus.VERIFIED
    assert execution.accepted is True
    assert execution.observed is True
    assert execution.verified is True
    assert connector.session.is_verified("read")
    assert execution.artifacts[0].payload["operation"] == "read"
    assert execution.artifacts[0].payload["relative_path"] == "notes.txt"
    assert execution.artifacts[0].payload["content"] == "hello from the real workspace"
    assert connector.backend.is_live() is True


def test_m2_real_workspace_read_preserves_redaction_and_fingerprint(tmp_path: Path):
    source = tmp_path / "secrets.txt"
    source.write_text("api_key=SUPER-SECRET\npublic=yes", encoding="utf-8")

    connector = BoundedWorkflowConnector(
        backend=__import__(
            "autonomous_agent.workflow.real_backend",
            fromlist=["ControlledRealWorkflowBackend"],
        ).ControlledRealWorkflowBackend(
            WorkspaceRealWorkflowAdapter(WorkspaceConnector(tmp_path))
        ),
        known_capability_ids=("filesystem:read",),
    )
    connector.validate(_pipeline("secrets.txt"))
    execution = connector.execute_step("read")

    artifact = execution.artifacts[0].payload
    assert execution.status is VerificationStatus.VERIFIED
    assert artifact["redacted"] is True
    assert "SUPER-SECRET" not in artifact["content"]
    assert artifact["content"].endswith("public=yes")
    assert artifact["fingerprint"]


def test_m2_workspace_adapter_rejects_unsupported_capability():
    adapter = WorkspaceRealWorkflowAdapter(
        WorkspaceConnector(Path.cwd()),
    )
    try:
        adapter.describe("filesystem:write", "write")
    except Exception as exc:
        assert "does not expose" in str(exc)
    else:
        raise AssertionError("M2 read-only adapter must not expose writes")


def test_m2_workspace_read_blocks_root_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    connector = BoundedWorkflowConnector(
        backend=__import__(
            "autonomous_agent.workflow.real_backend",
            fromlist=["ControlledRealWorkflowBackend"],
        ).ControlledRealWorkflowBackend(
            WorkspaceRealWorkflowAdapter(WorkspaceConnector(tmp_path))
        ),
        known_capability_ids=("filesystem:read",),
    )

    connector.validate(_pipeline("../outside.txt"))
    execution = connector.execute_step("read")

    assert execution.accepted is False
    assert execution.verified is False
    assert execution.status is VerificationStatus.FAILED


def test_m2_workspace_read_does_not_require_approval(tmp_path: Path):
    (tmp_path / "readme.txt").write_text("safe", encoding="utf-8")
    connector = BoundedWorkflowConnector(
        backend=__import__(
            "autonomous_agent.workflow.real_backend",
            fromlist=["ControlledRealWorkflowBackend"],
        ).ControlledRealWorkflowBackend(
            WorkspaceRealWorkflowAdapter(WorkspaceConnector(tmp_path))
        ),
        known_capability_ids=("filesystem:read",),
    )
    connector.validate(_pipeline("readme.txt"))
    execution = connector.execute_step("read")

    assert execution.succeeded is True
    assert connector.session.approved_steps == ()