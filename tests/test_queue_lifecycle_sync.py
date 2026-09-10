from pathlib import Path

from autonomous_agent.action_lifecycle import LifecycleState
from autonomous_agent.lifecycle_integration import record_transition
from autonomous_agent.queue_lifecycle_sync import has_blocking_drift, reconcile_queue_lifecycle


def _seed(path: Path, action_id: str, through: str) -> None:
    states = ["proposed", "validated", "tested", "secured", "policy_checked", "approved", "claimed", "executed", "verified", "completed"]
    for current, target in zip(states, states[1:]):
        ok, reason = record_transition(path, action_id, current, target)
        assert ok, reason
        if target == through:
            return


def _queue(path: Path, action_id: str, status: str = "pending") -> None:
    path.write_text(
        '[{"id":"%s","task":"inspect repository","steps":["inspect repository"],"risk":"low","reason":"test","status":"%s"}]\n'
        % (action_id, status),
        encoding="utf-8",
    )


def test_consistent_pending_approved_state(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    _seed(lifecycle, "a1", "approved")
    _queue(queue, "a1")

    decisions = reconcile_queue_lifecycle(queue, lifecycle)
    assert decisions[0].decision == "consistent"
    assert decisions[0].lifecycle_state is LifecycleState.APPROVED
    assert not has_blocking_drift(decisions)


def test_completed_lifecycle_with_pending_queue_is_drift(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    _seed(lifecycle, "a2", "completed")
    _queue(queue, "a2")

    decisions = reconcile_queue_lifecycle(queue, lifecycle)
    assert decisions[0].decision == "drift"
    assert has_blocking_drift(decisions)


def test_blocked_lifecycle_with_blocked_queue_is_consistent(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    _seed(lifecycle, "a3", "approved")
    ok, reason = record_transition(lifecycle, "a3", "approved", "blocked")
    assert ok, reason
    _queue(queue, "a3", "blocked")

    decisions = reconcile_queue_lifecycle(queue, lifecycle)
    assert decisions[0].decision == "consistent"
    assert decisions[0].lifecycle_state is LifecycleState.BLOCKED


def test_missing_lifecycle_history_is_drift(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    _queue(queue, "missing")

    decisions = reconcile_queue_lifecycle(queue, lifecycle)
    assert decisions[0].decision == "unchanged"


def test_corrupt_lifecycle_fails_closed(tmp_path: Path):
    lifecycle = tmp_path / "lifecycle.jsonl"
    queue = tmp_path / "queue.json"
    lifecycle.write_text("not-json\n", encoding="utf-8")
    _queue(queue, "a4")

    decisions = reconcile_queue_lifecycle(queue, lifecycle)
    assert decisions[0].decision == "blocked"
    assert has_blocking_drift(decisions)
