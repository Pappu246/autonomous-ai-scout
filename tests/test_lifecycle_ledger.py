from pathlib import Path

from autonomous_agent.action_lifecycle import LifecycleState
from autonomous_agent.lifecycle_ledger import append_transition, load_action_events, verify_ledger


ACTION_ID = "action-123"


def _complete_ledger(path: Path) -> None:
    transitions = [
        ("proposed", "validated"),
        ("validated", "tested"),
        ("tested", "secured"),
        ("secured", "policy_checked"),
        ("policy_checked", "approved"),
        ("approved", "claimed"),
        ("claimed", "executed"),
        ("executed", "verified"),
        ("verified", "completed"),
    ]
    for current, target in transitions:
        append_transition(path, ACTION_ID, current, target)


def test_ledger_records_ordered_transitions(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    _complete_ledger(path)
    assert verify_ledger(path)
    events = load_action_events(path, ACTION_ID)
    assert len(events) == 9
    assert events[0].from_state == "proposed"
    assert events[-1].to_state == "completed"
    assert [event.sequence for event in events] == list(range(1, 10))


def test_ledger_rejects_invalid_transition(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    append_transition(path, ACTION_ID, LifecycleState.PROPOSED, LifecycleState.VALIDATED)
    try:
        append_transition(path, ACTION_ID, LifecycleState.PROPOSED, LifecycleState.TESTED)
    except ValueError as exc:
        assert "invalid lifecycle transition" in str(exc)
    else:
        raise AssertionError("invalid transition was accepted")


def test_ledger_detects_tampering(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    _complete_ledger(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[4] = lines[4].replace("approved", "blocked", 1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not verify_ledger(path)
    assert load_action_events(path, ACTION_ID) == ()


def test_ledger_rejects_wrong_first_state(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    try:
        append_transition(path, ACTION_ID, "validated", "tested")
    except ValueError as exc:
        assert "first ledger transition" in str(exc)
    else:
        raise AssertionError("ledger accepted a non-proposed first state")


def _append_lifecycle_records(path_str: str, worker: int) -> None:
    from pathlib import Path
    from autonomous_agent.lifecycle_ledger import append_transition
    path = Path(path_str)
    action_id = f"action-{worker}"
    append_transition(path, action_id, "proposed", "validated")
    append_transition(path, action_id, "validated", "tested")


def test_lifecycle_ledger_survives_concurrent_processes(tmp_path: Path):
    from multiprocessing import Process
    path = tmp_path / "lifecycle.jsonl"
    workers = [Process(target=_append_lifecycle_records, args=(str(path), worker)) for worker in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert verify_ledger(path)
    for worker in range(4):
        events = load_action_events(path, f"action-{worker}")
        assert [event.sequence for event in events] == [1, 2]
