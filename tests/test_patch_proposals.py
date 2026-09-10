import pytest

from autonomous_agent.patch_proposals import build_patch_proposal


def test_patch_proposal_is_bounded_and_not_applied():
    proposal = build_patch_proposal("fix the bug", tuple(f"step {i}" for i in range(20)))
    assert len(proposal.steps) == 12
    assert proposal.status == "proposed"
    assert proposal.applied is False
    assert proposal.test_command == "python -m pytest -q"


def test_same_request_gets_stable_id():
    first = build_patch_proposal("improve tests", ("inspect", "test"))
    second = build_patch_proposal("improve tests", ("inspect", "test"))
    assert first.id == second.id


def test_sensitive_step_is_rejected():
    with pytest.raises(ValueError):
        build_patch_proposal("improve project", ("deploy the change",))


def test_blank_steps_are_removed():
    proposal = build_patch_proposal("inspect project", ("", "inspect files", "  "))
    assert proposal.steps == ("inspect files",)
