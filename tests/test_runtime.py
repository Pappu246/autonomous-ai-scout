from pathlib import Path

from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.computer.ai_controller import ComputerUseResult
from autonomous_agent.computer.backend import MockComputerBackend
from autonomous_agent.computer.connector import BoundedComputerConnector
from autonomous_agent.runtime import _plan_for_request, run_task
from autonomous_agent.capability_policy import Capability


def test_runtime_maps_safe_requests_to_bounded_plans():
    plan, grants = _plan_for_request("run the tests")
    assert plan.executable is True
    assert grants == (Capability.INSPECT, Capability.TEST)
    assert [step.tool_name for step in plan.steps] == ["github.inspect", "tests.run"]


def test_runtime_executes_safe_inspection_end_to_end(tmp_path: Path):
    result = run_task(
        "inspect repository",
        root=tmp_path,
        audit_path=tmp_path / "runtime.jsonl",
        execution_id="runtime-test",
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.results
    assert result.results[0].verification_status == "verified"



def test_runtime_routes_computer_use_through_canonical_executor(tmp_path: Path, monkeypatch):
    connector = BoundedComputerConnector(backend=MockComputerBackend())

    def fake_run(self, task, *, approved=False, max_turns=20, verify_final_state=True):
        assert approved is True
        assert "computer" in task.lower()
        return ComputerUseResult(
            "completed_verified",
            "verified test computer-use task",
            2,
            1,
            response_id="resp-test",
            final_text="VERIFIED",
        )

    monkeypatch.setattr(
        "autonomous_agent.computer.ai_controller.OpenAIComputerUseController.run",
        fake_run,
    )
    result = run_task(
        "control the computer and complete this task",
        root=tmp_path,
        audit_path=tmp_path / "runtime-computer.jsonl",
        execution_id="runtime-computer-test",
        computer_connector=connector,
        computer_request={"computer.use": {"task": "control the computer and complete this task", "max_turns": 3}},
        granted=(Capability.COMPUTER,),
        explicitly_approved=True,
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.results
    assert result.results[0].verification_status == "verified"


def test_runtime_fails_closed_for_unknown_request_only_after_safe_inspection():
    plan, grants = _plan_for_request("do something unspecified")
    assert plan.executable is True
    assert grants == (Capability.INSPECT,)
