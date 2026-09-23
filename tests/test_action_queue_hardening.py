from pathlib import Path

from autonomous_agent.action_queue import ActionStatus, build_action_proposal


def test_non_sensitive_action_proposal_is_not_marked_completed_before_execution():
    proposal = build_action_proposal("inspect project", ("list files", "run tests"))
    assert proposal.requires_approval is False
    assert proposal.status is ActionStatus.PROPOSED
    assert "no execution has occurred" in proposal.reason
