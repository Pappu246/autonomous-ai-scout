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


def test_malformed_persisted_queue_fails_closed(tmp_path: Path):
    path = tmp_path / "queue.json"
    path.write_text(
        '[{"id":"action-1","task":"inspect repository","steps":[],"risk":"low","reason":"ok","status":"pending","created_at":"not-a-timestamp"}]',
        encoding="utf-8",
    )
    from autonomous_agent.action_queue import load_queue
    import pytest
    with pytest.raises(ValueError, match="invalid creation timestamp"):
        load_queue(path)


def test_persisted_queue_with_unsanitized_secret_fails_closed(tmp_path: Path):
    path = tmp_path / "queue.json"
    path.write_text(
        '[{"id":"action-1","task":"inspect token=SUPERSECRET","steps":[],"risk":"low","reason":"ok","status":"pending","created_at":""}]',
        encoding="utf-8",
    )
    from autonomous_agent.action_queue import load_queue
    import pytest
    with pytest.raises(ValueError, match="unsanitized task"):
        load_queue(path)
