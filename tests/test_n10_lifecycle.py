from pathlib import Path
import pytest
from autonomous_agent.action_lifecycle import LifecycleState,advance,can_transition,authorize_lifecycle_completion,initial_state
from autonomous_agent.action_queue import PendingAction

def test_n10_lifecycle_requires_ordered_gates():
    state=LifecycleState.PROPOSED
    for target in [LifecycleState.VALIDATED,LifecycleState.TESTED,LifecycleState.SECURED,LifecycleState.POLICY_CHECKED,LifecycleState.APPROVED,LifecycleState.CLAIMED,LifecycleState.EXECUTED,LifecycleState.VERIFIED,LifecycleState.COMPLETED]:
        state=advance(state,target)
    assert state is LifecycleState.COMPLETED

def test_n10_lifecycle_rejects_skip():
    decision=can_transition(LifecycleState.PROPOSED,LifecycleState.APPROVED)
    assert not decision.allowed
    assert "invalid lifecycle transition" in decision.reason

def test_n10_lifecycle_completion_requires_approval_claim_and_verification():
    required=[LifecycleState.PROPOSED,LifecycleState.VALIDATED,LifecycleState.TESTED,LifecycleState.SECURED,LifecycleState.POLICY_CHECKED,LifecycleState.APPROVED,LifecycleState.CLAIMED,LifecycleState.EXECUTED,LifecycleState.VERIFIED]
    assert authorize_lifecycle_completion(required,approval_present=True,claim_present=True,post_change_verified=True).allowed
    assert not authorize_lifecycle_completion(required,approval_present=False,claim_present=True,post_change_verified=True).allowed
    assert not authorize_lifecycle_completion(required,approval_present=True,claim_present=False,post_change_verified=True).allowed
    assert not authorize_lifecycle_completion(required,approval_present=True,claim_present=True,post_change_verified=False).allowed

def test_n10_initial_state_accepts_only_pending_or_approved_action():
    action=PendingAction.create("a1","inspect repository",["github.inspect"])
    assert initial_state(action).state is LifecycleState.PROPOSED
    invalid=PendingAction.create("a2","inspect repository",["github.inspect"],status="executed")
    assert not initial_state(invalid).allowed

def test_n10_initial_state_rejects_incomplete_action():
    invalid=PendingAction.create("","",[])
    assert not initial_state(invalid).allowed
