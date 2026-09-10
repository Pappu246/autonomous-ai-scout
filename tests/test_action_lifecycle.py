import pytest

from autonomous_agent.action_lifecycle import (
    LifecycleState,
    advance,
    authorize_lifecycle_completion,
    can_transition,
    initial_state,
)
from autonomous_agent.action_queue import PendingAction


def action(status="pending"):
    return PendingAction("a1", "inspect project", ("inspect", "test"), "low", "safe", status, "2026-01-01T00:00:00+00:00")


def full_sequence():
    return [
        LifecycleState.PROPOSED,
        LifecycleState.VALIDATED,
        LifecycleState.TESTED,
        LifecycleState.SECURED,
        LifecycleState.POLICY_CHECKED,
        LifecycleState.APPROVED,
        LifecycleState.CLAIMED,
        LifecycleState.EXECUTED,
        LifecycleState.VERIFIED,
    ]


def test_valid_transition_chain():
    state = LifecycleState.PROPOSED
    for target in full_sequence()[1:]:
        decision = can_transition(state, target)
        assert decision.allowed
        state = advance(state, target)
    assert state is LifecycleState.VERIFIED


def test_invalid_skip_transition_is_blocked():
    decision = can_transition(LifecycleState.PROPOSED, LifecycleState.TESTED)
    assert not decision.allowed
    with pytest.raises(ValueError):
        advance(LifecycleState.PROPOSED, LifecycleState.TESTED)


def test_terminal_states_cannot_transition():
    assert not can_transition(LifecycleState.BLOCKED, LifecycleState.PROPOSED).allowed
    assert not can_transition(LifecycleState.COMPLETED, LifecycleState.PROPOSED).allowed


def test_initial_state_requires_complete_pending_or_approved_action():
    assert initial_state(action()).state is LifecycleState.PROPOSED
    assert initial_state(action("approved")).allowed
    assert not initial_state(action("completed")).allowed
    assert not initial_state(PendingAction("", "task", ("inspect",), "low", "reason")).allowed


def test_completion_requires_full_order_and_gates():
    decision = authorize_lifecycle_completion(
        full_sequence(), approval_present=True, claim_present=True, post_change_verified=True
    )
    assert decision.allowed
    assert decision.state is LifecycleState.COMPLETED


@pytest.mark.parametrize(
    "kwargs",
    [
        {"states": full_sequence()[:-1], "approval_present": True, "claim_present": True, "post_change_verified": True},
        {"states": full_sequence(), "approval_present": False, "claim_present": True, "post_change_verified": True},
        {"states": full_sequence(), "approval_present": True, "claim_present": False, "post_change_verified": True},
        {"states": full_sequence(), "approval_present": True, "claim_present": True, "post_change_verified": False},
        {"states": ["proposed", "tested"], "approval_present": True, "claim_present": True, "post_change_verified": True},
    ],
)
def test_completion_fails_closed(kwargs):
    decision = authorize_lifecycle_completion(**kwargs)
    assert not decision.allowed
    assert decision.state is LifecycleState.BLOCKED
