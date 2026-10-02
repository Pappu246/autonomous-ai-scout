from pathlib import Path

from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.mission_control import MissionController


def test_submit_plan_persists_bounded_dag(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)

    mission = controller.submit_plan(
        "inspect then report",
        [
            {"node_id": "inspect", "task": "inspect repository", "depends_on": []},
            {"node_id": "report", "task": "research the project", "depends_on": ["inspect"]},
        ],
    )

    assert mission.state == "pending"
    assert len(mission.steps) == 2
    assert mission.steps[1]["depends_on"] == ("inspect",)


def test_submit_plan_blocks_invalid_dependency(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)

    mission = controller.submit_plan(
        "invalid mission",
        [{"node_id": "report", "task": "research", "depends_on": ["missing"]}],
    )

    assert mission.state == "blocked"
    assert controller.queue.list() == ()


def test_run_once_executes_dag_in_dependency_order(monkeypatch, tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit_plan(
        "two-step mission",
        [
            {"node_id": "a", "task": "inspect repository", "depends_on": []},
            {"node_id": "b", "task": "research the project", "depends_on": ["a"]},
        ],
    )
    calls: list[str] = []

    def fake_run_task(task, **kwargs):
        calls.append(task)
        return type("Result", (), {"state": ExecutionState.VERIFIED, "reason": "verified"})()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", fake_run_task)
    result = controller.run_once()

    assert result is not None
    assert result.state == "verified"
    assert calls == ["inspect repository", "research the project"]
