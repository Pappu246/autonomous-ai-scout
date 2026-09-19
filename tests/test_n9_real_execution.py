from pathlib import Path

from autonomous_agent.execution_engine import ExecutionState, execute_plan
from autonomous_agent.task_planner import plan_task
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_audit import verify_execution_audit


def test_n9_executes_a_multi_step_safe_plan_and_verifies_every_result(tmp_path: Path):
    plan = plan_task(
        "run the tests",
        granted=[Capability.INSPECT, Capability.TEST],
    )
    assert plan.executable
    assert [step.tool_name for step in plan.steps] == ["github.inspect", "tests.run"]

    audit = tmp_path / "n9-execution.jsonl"
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT, Capability.TEST],
        audit_path=audit,
        execution_id="n9-multi-step",
    )

    assert result.state is ExecutionState.VERIFIED
    assert result.attempts == 2
    assert len(result.results) == 2
    assert all(item.success for item in result.results)
    assert all(item.verification_status == "verified" for item in result.results)
    assert verify_execution_audit(audit)


def test_n9_rejects_replay_of_an_interrupted_execution(tmp_path: Path):
    plan = plan_task("inspect repository", granted=[Capability.INSPECT])
    assert plan.executable

    audit = tmp_path / "n9-recovery.jsonl"
    from autonomous_agent.execution_audit import append_execution_record

    append_execution_record(
        audit,
        {"execution_id": "n9-interrupted", "state": "running", "timestamp": "now"},
    )

    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        execution_id="n9-interrupted",
    )

    assert result.state is ExecutionState.RECOVERY_REQUIRED
    assert "fresh authorization" in result.reason