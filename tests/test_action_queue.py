from pathlib import Path

from autonomous_agent.action_queue import ActionStatus, build_action_proposal, enqueue_proposal, load_queue


def test_sensitive_task_cannot_lower_approval():
    proposal = build_action_proposal("deploy the app", ("inspect",), llm_requires_approval=False)
    assert proposal.requires_approval is True
    assert proposal.status is ActionStatus.PROPOSED


def test_sensitive_step_requires_approval():
    proposal = build_action_proposal("inspect project", ("delete temporary files",), llm_requires_approval=False)
    assert proposal.requires_approval is True
    assert proposal.status is ActionStatus.PROPOSED


def test_read_only_plan_can_complete():
    proposal = build_action_proposal("inspect project", ("list files", "run tests"))
    assert proposal.requires_approval is False
    assert proposal.status is ActionStatus.COMPLETED


def test_approval_proposal_is_persisted_and_deduplicated(tmp_path: Path):
    path = tmp_path / "queue.json"
    proposal = build_action_proposal("fix bug", ("inspect", "test", "edit source"))
    first = enqueue_proposal(path, proposal, "medium")
    second = enqueue_proposal(path, proposal, "medium")
    queue = load_queue(path)
    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert len(queue) == 1
    assert queue[0].status == "pending"
