from pathlib import Path

import pytest

from autonomous_agent.action_queue import ActionStatus, build_action_proposal, enqueue_proposal, load_queue
from autonomous_agent.approval import pending_actions, set_decision
from autonomous_agent.approval_audit import read_audit


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


def test_explicit_approval_changes_only_queue_state(tmp_path: Path):
    path = tmp_path / "queue.json"
    proposal = build_action_proposal("fix bug", ("inspect", "edit source"))
    action = enqueue_proposal(path, proposal, "medium")
    assert action is not None
    updated = set_decision(path, action.id, "approved")
    assert updated.status == "approved"
    assert pending_actions(path) == []
    assert load_queue(path)[0].steps == proposal.steps


def test_rejection_is_recorded(tmp_path: Path):
    path = tmp_path / "queue.json"
    proposal = build_action_proposal("change code", ("edit source",))
    action = enqueue_proposal(path, proposal)
    assert action is not None
    assert set_decision(path, action.id, "rejected").status == "rejected"


def test_terminal_approval_cannot_be_changed(tmp_path: Path):
    path = tmp_path / "queue.json"
    proposal = build_action_proposal("change code", ("edit source",))
    action = enqueue_proposal(path, proposal)
    assert action is not None
    set_decision(path, action.id, "approved")
    with pytest.raises(ValueError, match="already approved"):
        set_decision(path, action.id, "rejected")


def test_invalid_decision_is_rejected(tmp_path: Path):
    path = tmp_path / "queue.json"
    proposal = build_action_proposal("change code", ("edit source",))
    action = enqueue_proposal(path, proposal)
    assert action is not None
    with pytest.raises(ValueError):
        set_decision(path, action.id, "run")


def test_unknown_action_cannot_be_approved(tmp_path: Path):
    with pytest.raises(KeyError):
        set_decision(tmp_path / "queue.json", "missing", "approved")


def test_approval_can_write_a_metadata_only_audit_record(tmp_path: Path):
    queue_path = tmp_path / "queue.json"
    audit_path = tmp_path / "audit.jsonl"
    proposal = build_action_proposal("fix bug", ("inspect", "edit source"))
    action = enqueue_proposal(queue_path, proposal)
    assert action is not None
    set_decision(queue_path, action.id, "approved", audit_path=audit_path)
    records = read_audit(audit_path)
    assert len(records) == 1
    assert records[0]["action_id"] == action.id
    assert records[0]["decision"] == "approved"
    assert "timestamp" in records[0]
    assert "hash" in records[0]
    assert "task" not in records[0]
    assert "steps" not in records[0]


def test_audit_records_chain_in_order(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    from autonomous_agent.approval_audit import append_decision

    append_decision(audit_path, "one", "approved")
    append_decision(audit_path, "two", "rejected")
    records = read_audit(audit_path)
    assert records[0]["previous_hash"] == ""
    assert records[1]["previous_hash"] == records[0]["hash"]
