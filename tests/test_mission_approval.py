from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.mission_control import MissionController
from autonomous_agent.approval_store import create_approval, load_approval
from autonomous_agent.approved_executor import claim_approval


def test_side_effect_mission_enters_durable_approval_queue(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)

    mission = controller.submit("use the computer to complete this task")

    assert mission.state == "requires_approval"
    assert mission.approval_action_id
    assert controller.queue.list() == ()
    action = next(
        item
        for item in controller.approval_queue_path.read_text(encoding="utf-8").splitlines()
        if mission.approval_action_id in item
    )
    assert mission.approval_action_id in action


def test_approved_mission_is_activated_only_after_approval(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("use the computer to complete this task")
    action_id = mission.approval_action_id

    try:
        controller.activate_approved_action(action_id)
    except ValueError as exc:
        assert "explicitly approved" in str(exc)
    else:
        raise AssertionError("mission must not activate before approval")

    create_approval(
        controller.approval_queue_path,
        controller.approval_dir,
        action_id,
        audit_path=controller.approval_audit_path,
    )
    activated = controller.activate_approved_action(action_id)

    assert activated is not None
    assert activated.state == "pending"
    assert activated.approval_action_id == action_id
    assert len(controller.queue.list()) == 1


def test_approved_mission_passes_explicit_approval_and_computer_connector(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("use the computer to complete this task")
    create_approval(
        controller.approval_queue_path,
        controller.approval_dir,
        mission.approval_action_id,
        audit_path=controller.approval_audit_path,
    )
    activated = controller.activate_approved_action(mission.approval_action_id)
    assert activated is not None

    captured = {}

    def fake_run_task(task, **kwargs):
        captured.update(kwargs)
        return type(
            "Result",
            (),
            {
                "state": ExecutionState.VERIFIED,
                "reason": "verified test result",
                "attempts": 1,
            },
        )()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", fake_run_task)

    result = controller.run_once()

    assert result is not None
    assert result.state == "verified"
    assert captured["explicitly_approved"] is True
    assert captured["computer_connector"] is controller.computer_connector
    assert captured["computer_request"]["computer.use"]["task"] == mission.task


def test_expired_approval_cannot_activate_mission(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("use the computer to complete this task")
    create_approval(
        controller.approval_queue_path,
        controller.approval_dir,
        mission.approval_action_id,
        audit_path=controller.approval_audit_path,
        approved_at=datetime.now(timezone.utc) - timedelta(hours=25),
        ttl=timedelta(hours=24),
    )

    try:
        controller.activate_approved_action(mission.approval_action_id)
    except ValueError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("expired approval must not activate a mission")


def test_mission_approval_is_claimed_once_at_execution(tmp_path: Path, monkeypatch) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("use the computer to complete this task")
    create_approval(
        controller.approval_queue_path,
        controller.approval_dir,
        mission.approval_action_id,
        audit_path=controller.approval_audit_path,
    )
    activated = controller.activate_approved_action(mission.approval_action_id)
    assert activated is not None

    monkeypatch.setattr(
        "autonomous_agent.mission_control.run_task",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {
                "state": ExecutionState.VERIFIED,
                "reason": "verified test result",
                "attempts": 1,
                "results": (),
            },
        )(),
    )
    result = controller.run_once()
    assert result is not None
    approval = load_approval(controller.approval_dir, mission.approval_action_id)
    claim_again = claim_approval(approval, controller.approval_claim_dir)
    assert claim_again.allowed is False
    assert "consumed" in claim_again.reason
