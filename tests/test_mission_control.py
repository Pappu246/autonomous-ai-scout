from __future__ import annotations

from pathlib import Path

from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.mission_control import MissionController


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
    assert "blocked" in mission.reason.lower()
    assert controller.queue.list() == ()


def test_run_once_updates_mission_from_verified_runtime(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")

    def fake_run_task(task, **kwargs):
        assert task == "inspect repository"
        assert kwargs["execution_id"] == mission.execution_id
        return type(
            "Result",
            (),
            {
                "state": ExecutionState.VERIFIED,
                "reason": "verified by test",
            },
        )()

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
        return type(
            "Result",
            (),
            {
                "state": ExecutionState.BLOCKED,
                "reason": "connector unavailable",
            },
        )()

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


def test_multi_step_mission_persists_verified_progress_and_resume_does_not_replay(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit_plan(
        "two-step verification mission",
        (
            {"node_id": "step-1", "task": "inspect repository", "depends_on": ()},
            {"node_id": "step-2", "task": "inspect repository", "depends_on": ("step-1",)},
        ),
    )
    assert mission.state == "pending"

    calls: list[str] = []
    attempt = {"count": 0}

    def first_run(task, **kwargs):
        calls.append(kwargs["execution_id"])
        attempt["count"] += 1
        if attempt["count"] == 1:
            return type("Result", (), {
                "state": ExecutionState.VERIFIED,
                "reason": "step one verified",
            })()
        return type("Result", (), {
            "state": ExecutionState.BLOCKED,
            "reason": "simulated connector interruption",
        })()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", first_run)
    failed = controller.run_once()

    assert failed is not None
    assert failed.state == "failed"
    assert failed.completed_steps == ("step-1",)
    assert len(calls) == 2

    resumed = controller.resume(mission.mission_id)
    assert resumed.state == "pending"
    assert resumed.completed_steps == ("step-1",)

    def second_run(task, **kwargs):
        calls.append(kwargs["execution_id"])
        assert kwargs["execution_id"].endswith(":step-2")
        return type("Result", (), {
            "state": ExecutionState.VERIFIED,
            "reason": "step two verified",
        })()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", second_run)
    completed = controller.run_once()

    assert completed is not None
    assert completed.state == "verified"
    assert completed.completed_steps == ("step-1", "step-2")
    assert calls[-1].endswith(":step-2")


def test_resume_rejects_verified_mission(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")
    controller.queue.claim_next()
    controller.store.put(mission.__class__(
        mission.mission_id, mission.task_id, mission.execution_id, mission.task,
        "verified", "done", mission.created_at, mission.updated_at,
        mission.attempts, mission.specialist_role, mission.steps, mission.completed_steps,
    ))

    try:
        controller.resume(mission.mission_id)
    except ValueError as exc:
        assert "cannot be resumed" in str(exc)
    else:
        raise AssertionError("verified mission must not be resumed")
