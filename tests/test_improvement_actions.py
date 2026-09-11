from __future__ import annotations

from pathlib import Path

from autonomous_agent.continuous_improvement import ImprovementRisk, build_proposal
from autonomous_agent.improvement_actions import enqueue_improvement, proposal_to_action
from autonomous_agent.models import ProjectFinding


def proposal():
    return build_proposal(
        "owner/repo",
        ProjectFinding(
            repository="owner/repo",
            severity="high",
            title="Open regression",
            detail="CI failure detected",
            recommendation="Add a focused regression test",
            confidence=0.9,
        ),
    )


def test_proposal_binds_to_existing_approval_queue():
    action = proposal_to_action(proposal())
    assert action.requires_approval is True
    assert action.status.value == "proposed"
    assert any("approval/change boundary" in step for step in action.steps)


def test_enqueue_does_not_execute_or_create_pr(tmp_path: Path):
    queued = enqueue_improvement(tmp_path / "queue.json", proposal())
    assert queued is not None
    assert queued.status == "pending"
    assert queued.risk == ImprovementRisk.HIGH.name.lower()
    assert "pull request" not in queued.task.lower()


def test_enqueue_is_idempotent_for_same_proposal(tmp_path: Path):
    path = tmp_path / "queue.json"
    first = enqueue_improvement(path, proposal())
    second = enqueue_improvement(path, proposal())
    assert first is not None and second is not None
    assert first.id == second.id
