from pathlib import Path

from autonomous_agent.action_queue import ActionStatus, build_action_proposal


def test_non_sensitive_action_proposal_is_not_marked_completed_before_execution():
    proposal = build_action_proposal("inspect project", ("list files", "run tests"))
    assert proposal.requires_approval is False
    assert proposal.status is ActionStatus.PROPOSED
    assert "no execution has occurred" in proposal.reason


def test_action_proposal_redacts_secret_material_before_storage():
    proposal = build_action_proposal(
        "inspect token=SUPERSECRET",
        ("read api_key=ABC123",),
        llm_requires_approval=True,
    )
    assert "SUPERSECRET" not in proposal.task
    assert "ABC123" not in proposal.steps[0]
    assert "[REDACTED]" in proposal.task
    assert "[REDACTED]" in proposal.steps[0]
