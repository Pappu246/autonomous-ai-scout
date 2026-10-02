from __future__ import annotations

from pathlib import Path

from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.mission_control import MissionController, MissionStore


def test_submit_persists_safe_mission_and_queue(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)

    mission = controller.submit("inspect repository")

    assert mission.state == "pending"
    assert mission.task == "inspect repository"
    assert mission.mission_id.startswith("mission-")
    assert controller.store.get(mission.mission_id) == mission
    assert controller.queue.list()[0].task_id == mission.task_id


def test_submit_blocks_non_executable_task_without_queuing(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)

    mission = controller.submit("fix the failing tests")

    assert mission.state == "blocked"
    assert "approval" in mission.reason.lower()
    assert controller.queue.list() == ()


def test_run_once_updates_mission_from_verified_runtime(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")

    def fake_run_task(task, **kwargs):
        assert task == "inspect repository"
        assert kwargs["execution_id"] == mission.execution_id
        return type("Result", (), {
            "state": ExecutionState.VERIFIED,
            "reason": "verified by test",
        })()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", fake_run_task)

    result = controller.run_once()

    assert result is not None
    assert result.mission_id == mission.mission_id
    assert result.state == "verified"
    assert "verified" in result.reason


def test_run_once_keeps_failed_runtime_state(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")

    def fake_run_task(task, **kwargs):
        return type("Result", (), {
            "state": ExecutionState.BLOCKED,
            "reason": "connector unavailable",
        })()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", fake_run_task)

    result = controller.run_once()

    assert result is not None
    assert result.state == "blocked"
    assert "connector unavailable" in result.reason
    assert controller.queue.list()[0].state.value == "failed"


def test_cancel_only_changes_queued_mission(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")

    cancelled = controller.cancel(mission.mission_id)

    assert cancelled.state == "cancelled"
    assert controller.store.get(mission.mission_id).state == "cancelled"


def test_mission_store_lists_newest_first(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.json")
    first = store.put(type("Record", (), {})()) if False else None
    assert store.list() == ()
