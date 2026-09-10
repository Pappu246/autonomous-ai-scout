from datetime import datetime, timezone
from pathlib import Path

from autonomous_agent.action_lifecycle import LifecycleState
from autonomous_agent.approved_executor import ApprovalRecord, execute_approved_action
from autonomous_agent.action_queue import PendingAction
from autonomous_agent.lifecycle_integration import record_transition, require_state


ACTION_ID = "action-integration"


def _action() -> PendingAction:
    return PendingAction(
        id=ACTION_ID,
        task="inspect repository",
        steps=("inspect repository",),
        risk="low",
        reason="safe diagnostics",
        status="approved",
        created_at="2026-09-10T10:00:00+00:00",
    )


def _seed_to_approved(path: Path) -> None:
    transitions = [
        ("proposed", "validated"),
        ("validated", "tested"),
        ("tested", "secured"),
        ("secured", "policy_checked"),
        ("policy_checked", "approved"),
    ]
    for current, target in transitions:
        ok, reason = record_transition(path, ACTION_ID, current, target)
        assert ok, reason


def test_require_state_fails_closed_for_missing_or_wrong_state(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    ok, reason = require_state(path, ACTION_ID, LifecycleState.APPROVED)
    assert not ok
    assert "required" in reason

    _seed_to_approved(path)
    ok, _ = require_state(path, ACTION_ID, LifecycleState.APPROVED)
    assert ok
    ok, _ = require_state(path, ACTION_ID, LifecycleState.CLAIMED)
    assert not ok


def test_execution_requires_and_advances_persisted_lifecycle(tmp_path: Path, monkeypatch):
    action = _action()
    approval = ApprovalRecord.for_action(
        action,
        "opaque-token",
        datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
    )
    lifecycle = tmp_path / "lifecycle.jsonl"
    _seed_to_approved(lifecycle)

    calls = []

    class Result:
        operation = "inspect"
        success = True
        exit_status = 0
        output = "inspect ok"
        output_truncated = False
        command = ("inspect",)
        verification_status = "verified"
        started_at = "2026-09-10T10:01:00+00:00"
        finished_at = "2026-09-10T10:01:01+00:00"
        network_disabled = True

    def fake_run(operation, root, target=None):
        calls.append((operation, target))
        return Result()

    monkeypatch.setattr("autonomous_agent.approved_executor.run_safe_operation", fake_run)
    root = tmp_path / "project"
    root.mkdir()
    decision = execute_approved_action(
        action,
        approval,
        root,
        now=datetime(2026, 9, 10, 10, 1, tzinfo=timezone.utc),
        claim_store=tmp_path / "claims",
        lifecycle_path=lifecycle,
    )
    assert decision.allowed
    assert calls == [("inspect", None)]
    assert require_state(lifecycle, ACTION_ID, LifecycleState.EXECUTED)[0]


def test_execution_blocks_before_consuming_claim_when_ledger_is_tampered(tmp_path: Path, monkeypatch):
    action = _action()
    approval = ApprovalRecord.for_action(action, "opaque-token")
    lifecycle = tmp_path / "lifecycle.jsonl"
    _seed_to_approved(lifecycle)
    lines = lifecycle.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("validated", "blocked", 1)
    lifecycle.write_text("\n".join(lines) + "\n", encoding="utf-8")

    called = []
    monkeypatch.setattr("autonomous_agent.approved_executor.run_safe_operation", lambda *args: called.append(args))
    root = tmp_path / "project"
    root.mkdir()
    decision = execute_approved_action(
        action,
        approval,
        root,
        claim_store=tmp_path / "claims",
        lifecycle_path=lifecycle,
    )
    assert not decision.allowed
    assert "lifecycle ledger" in decision.reason
    assert called == []
    assert not (tmp_path / "claims").exists()


def test_failed_execution_is_terminally_blocked(tmp_path: Path, monkeypatch):
    action = _action()
    approval = ApprovalRecord.for_action(action, "opaque-token")
    lifecycle = tmp_path / "lifecycle.jsonl"
    _seed_to_approved(lifecycle)

    class Result:
        operation = "inspect"
        success = False
        exit_status = 1
        output = "sandbox failure"
        output_truncated = False
        command = ("inspect",)
        verification_status = "failed"
        started_at = "2026-09-10T10:01:00+00:00"
        finished_at = "2026-09-10T10:01:01+00:00"
        network_disabled = True

    monkeypatch.setattr("autonomous_agent.approved_executor.run_safe_operation", lambda *args: Result())
    root = tmp_path / "project"
    root.mkdir()
    decision = execute_approved_action(
        action,
        approval,
        root,
        claim_store=tmp_path / "claims",
        lifecycle_path=lifecycle,
    )
    assert not decision.allowed
    assert require_state(lifecycle, ACTION_ID, LifecycleState.BLOCKED)[0]
