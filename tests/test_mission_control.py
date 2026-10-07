from __future__ import annotations

from autonomous_agent.action_queue import load_queue

import json
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


def test_empty_mission_task_is_rejected(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)

    try:
        controller.submit("   ")
    except ValueError as exc:
        assert "mission task is required" in str(exc)
    else:
        raise AssertionError("empty mission task must be rejected")


def test_run_once_records_secret_safe_specialist_provider_route(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")

    def fake_run_task(task, **kwargs):
        return type(
            "Result",
            (),
            {
                "state": ExecutionState.VERIFIED,
                "reason": "verified by test",
                "results": (),
            },
        )()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", fake_run_task)

    result = controller.run_once()

    assert result is not None
    lines = controller.audit_path.read_text(encoding="utf-8").splitlines()
    route_events = [json.loads(line) for line in lines if json.loads(line).get("event") == "specialist_provider_route"]
    assert route_events
    event = route_events[-1]
    assert event["execution_id"] == mission.execution_id
    assert event["specialist_role"] == mission.specialist_role
    assert event["eligible"] == "False"
    assert event["provider"] == ""
    assert "endpoint" not in json.dumps(event).lower()
    assert "api_key" not in json.dumps(event).lower()


def test_rejected_mission_is_terminal(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("use the computer to complete this task")
    rejected = controller.reject_approved_action(mission.approval_action_id)
    assert rejected is not None
    assert rejected.state == "rejected"
    assert rejected.terminal is True


def test_timeline_surfaces_provider_route_telemetry(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")

    def fake_run_task(task, **kwargs):
        return type(
            "Result",
            (),
            {"state": ExecutionState.VERIFIED, "reason": "verified", "results": ()},
        )()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", fake_run_task)
    controller.run_once()

    timeline = controller.timeline(mission.mission_id)
    route_events = [item for item in timeline if item.get("event") == "specialist_provider_route"]
    assert route_events
    assert route_events[-1]["specialist_role"] == mission.specialist_role
    assert route_events[-1]["selection_only"] == "True"
    assert route_events[-1]["provider"] == ""


def test_run_once_surfaces_bounded_test_result_summary(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("run tests")

    def fake_run_task(task, **kwargs):
        return type(
            "Result",
            (),
            {
                "state": ExecutionState.FAILED,
                "reason": "tool execution failed after bounded retries: tests.run",
                "results": (
                    type(
                        "ToolResult",
                        (),
                        {
                            "operation": "test",
                            "success": False,
                            "verification_status": "failed",
                            "output": "network isolation unavailable; sandbox refused subprocess execution",
                        },
                    )(),
                ),
            },
        )()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", fake_run_task)
    result = controller.run_once()

    assert result is not None
    assert result.state == "failed"
    assert "tests.run" in result.reason
    assert "network isolation unavailable" in result.reason


def test_cancel_approval_pending_mission_invalidates_pending_approval(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("use the computer to complete this task")
    assert mission.state == "requires_approval"

    cancelled = controller.cancel(mission.mission_id)

    assert cancelled.state == "cancelled"
    action = next(
        item for item in load_queue(controller.approval_queue_path)
        if item.id == mission.approval_action_id
    )
    assert action.status == "rejected"
