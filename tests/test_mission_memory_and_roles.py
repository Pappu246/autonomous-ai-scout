from pathlib import Path

from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.mission_control import MissionController
from autonomous_agent.specialist_router import SpecialistRole


def test_mission_submission_records_specialist_role(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)

    mission = controller.submit("research the project and compare sources")

    assert mission.specialist_role == SpecialistRole.RESEARCH.value
    assert "specialist=research" in mission.reason


def test_mission_submission_reuses_persistent_memory_hint(tmp_path: Path) -> None:
    first = MissionController(root=tmp_path)
    first.memory.record_episode(
        first.MEMORY_PROJECT,
        "inspect repository test failures",
        outcome="verified",
        metadata={"specialist_role": SpecialistRole.CODING.value},
    )

    second = MissionController(root=tmp_path)
    mission = second.submit("inspect repository test failures")

    assert "recalled" in mission.reason


def test_mission_runtime_records_execution_memory(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")

    def fake_run_task(task, **kwargs):
        assert kwargs["memory"] is controller.memory.store
        assert kwargs["project"] == controller.MEMORY_PROJECT
        return type(
            "Result",
            (),
            {"state": ExecutionState.VERIFIED, "reason": "verified runtime"},
        )()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", fake_run_task)
    result = controller.run_once()

    assert result is not None
    assert result.state == "verified"
    matches = controller.memory.recall(controller.MEMORY_PROJECT, "inspect repository", limit=5)
    assert matches
