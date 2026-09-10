from pathlib import Path

from autonomous_agent.action_lifecycle import LifecycleState
from autonomous_agent.lifecycle_integration import record_transition, require_state
from autonomous_agent.startup_reconciliation import reconcile_startup


def _seed(path: Path, action_id: str, through: str) -> None:
    states = [
        "proposed",
        "validated",
        "tested",
        "secured",
        "policy_checked",
        "approved",
        "claimed",
        "executed",
        "verified",
        "completed",
    ]
    for current, target in zip(states, states[1:]):
        ok, reason = record_transition(path, action_id, current, target)
        assert ok, reason
        if target == through:
            return


def _queue(path: Path, action_id: str, status: str = "pending") -> None:
    path.write_text(
        '[{"id":"%s","task":"inspect repository","steps":["inspect repository"],"risk":"low","reason":"test","status":"%s","created_at":"2026-09-10T10:00:00+00:00"}]\n'
        % (action_id, status),
        encoding="utf-8",
    )


def test_startup_blocks_interrupted_claimed_action(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    _seed(lifecycle, "a1", "claimed")
    _queue(queue, "a1")

    decisions = reconcile_startup(queue, lifecycle)
    assert decisions[0].decision == "blocked"
    assert decisions[0].state is LifecycleState.BLOCKED
    assert require_state(lifecycle, "a1", LifecycleState.BLOCKED)[0]


def test_startup_blocks_interrupted_executed_action(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    _seed(lifecycle, "a2", "executed")
    _queue(queue, "a2")

    decisions = reconcile_startup(queue, lifecycle)
    assert decisions[0].state is LifecycleState.BLOCKED
    assert "terminally blocked" in decisions[0].reason


def test_startup_never_resumes_completed_action(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    _seed(lifecycle, "a3", "completed")
    _queue(queue, "a3")

    decisions = reconcile_startup(queue, lifecycle)
    assert decisions[0].decision == "terminal"
    assert decisions[0].state is LifecycleState.COMPLETED


def test_startup_fail_closes_corrupt_ledger(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    lifecycle.write_text("not-json\n", encoding="utf-8")
    _queue(queue, "a4")

    decisions = reconcile_startup(queue, lifecycle)
    assert decisions[0].decision == "blocked"
    assert "invalid" in decisions[0].reason


def test_startup_detects_orphaned_interrupted_lifecycle(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    _seed(lifecycle, "orphan", "claimed")
    _queue(queue, "other")

    decisions = reconcile_startup(queue, lifecycle)
    orphan = next(item for item in decisions if item.action_id == "orphan")
    assert orphan.decision == "blocked"
    assert orphan.state is LifecycleState.BLOCKED
