from pathlib import Path

from autonomous_agent.action_lifecycle import LifecycleState
from autonomous_agent.lifecycle_integration import record_transition
from autonomous_agent.lifecycle_ledger import verify_ledger
from autonomous_agent.lifecycle_recovery import inspect_recovery, recover_stale_execution


ACTION_ID = "recovery-action"


def _seed(path: Path, target: LifecycleState) -> None:
    transitions = [
        ("proposed", "validated"),
        ("validated", "tested"),
        ("tested", "secured"),
        ("secured", "policy_checked"),
        ("policy_checked", "approved"),
        ("approved", "claimed"),
    ]
    if target is LifecycleState.EXECUTED:
        transitions.append(("claimed", "executed"))
    for current, next_state in transitions:
        ok, reason = record_transition(path, ACTION_ID, current, next_state)
        assert ok, reason


def test_claimed_state_is_recoverable_and_blocks(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    _seed(path, LifecycleState.CLAIMED)
    decision = inspect_recovery(path, ACTION_ID)
    assert decision.recoverable
    assert decision.state is LifecycleState.CLAIMED

    recovered = recover_stale_execution(path, ACTION_ID)
    assert not recovered.recoverable
    assert recovered.state is LifecycleState.BLOCKED
    assert verify_ledger(path)
    assert inspect_recovery(path, ACTION_ID).state is LifecycleState.BLOCKED


def test_executed_state_is_recoverable_and_never_resumes(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    _seed(path, LifecycleState.EXECUTED)
    recovered = recover_stale_execution(path, ACTION_ID)
    assert not recovered.recoverable
    assert recovered.state is LifecycleState.BLOCKED
    assert "terminally blocked" in recovered.reason


def test_completed_and_pre_execution_states_are_not_recovered(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    _seed(path, LifecycleState.CLAIMED)
    record_transition(path, ACTION_ID, LifecycleState.CLAIMED, LifecycleState.EXECUTED)
    record_transition(path, ACTION_ID, LifecycleState.EXECUTED, LifecycleState.VERIFIED)
    record_transition(path, ACTION_ID, LifecycleState.VERIFIED, LifecycleState.COMPLETED)
    decision = inspect_recovery(path, ACTION_ID)
    assert not decision.recoverable
    assert decision.state is LifecycleState.COMPLETED

    pre_path = tmp_path / "pre.jsonl"
    for current, next_state in [
        ("proposed", "validated"),
        ("validated", "tested"),
        ("tested", "secured"),
        ("secured", "policy_checked"),
        ("policy_checked", "approved"),
    ]:
        record_transition(pre_path, ACTION_ID, current, next_state)
    decision = inspect_recovery(pre_path, ACTION_ID)
    assert not decision.recoverable
    assert decision.state is LifecycleState.APPROVED


def test_invalid_or_missing_ledger_fails_closed(tmp_path: Path):
    missing = inspect_recovery(tmp_path / "missing.jsonl", ACTION_ID)
    assert not missing.recoverable
    assert missing.state is LifecycleState.BLOCKED

    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_text("not-json\n", encoding="utf-8")
    decision = inspect_recovery(corrupt, ACTION_ID)
    assert not decision.recoverable
    assert "invalid" in decision.reason
