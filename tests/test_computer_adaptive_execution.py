from pathlib import Path

from autonomous_agent.adaptive_execution import execute_adaptive_plan
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_engine import ExecutionResult, ExecutionState
from autonomous_agent.sandbox import SandboxResult
from autonomous_agent.task_planner import plan_task

def test_adaptive_execution_forwards_computer_connector(monkeypatch, tmp_path: Path):
    plan = plan_task(
        "control the computer and complete this task",
        granted=[Capability.COMPUTER],
        explicitly_approved=True,
    )
    assert plan.executable is True
    captured = {}

    def fake_execute_plan(plan, root, **kwargs):
        captured.update(kwargs)
        return ExecutionResult(
            ExecutionState.VERIFIED,
            "verified",
            1,
            (
                SandboxResult(
                    "computer",
                    True,
                    0,
                    '{"verified": true}',
                    False,
                    ("COMPUTER", "computer_use"),
                    "verified",
                    "start",
                    "finish",
                    True,
                ),
            ),
            str(tmp_path / "child.jsonl"),
        )

    monkeypatch.setattr("autonomous_agent.adaptive_execution.execute_plan", fake_execute_plan)

    computer_connector = object()
    result = execute_adaptive_plan(
        plan,
        tmp_path,
        granted=[Capability.COMPUTER],
        explicitly_approved=True,
        audit_path=tmp_path / "adaptive-computer.jsonl",
        execution_id="adaptive-computer",
        computer_connector=computer_connector,
        computer_request={"computer.use": {"task": "control the computer", "max_turns": 2}},
    )

    assert result.state is ExecutionState.VERIFIED
    assert captured["computer_connector"] is computer_connector
    assert captured["computer_request"]["computer.use"]["max_turns"] == 2
