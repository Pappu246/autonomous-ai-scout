from pathlib import Path

from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_audit import verify_execution_audit
from autonomous_agent.execution_engine import ExecutionState, execute_plan, recover_execution
from autonomous_agent.task_plan_models import PlanRisk, TaskAuditRecord, TaskIntent, TaskPlan, TaskStep
from autonomous_agent.execution_audit import append_execution_record


def _safe_multi_step_plan() -> TaskPlan:
    steps = (
        TaskStep("step-1", "Inspect the approved workspace.", "github.inspect", PlanRisk.LOW, "authorized", "execute through sandbox", "verify result"),
        TaskStep("step-2", "Collect deterministic project metrics.", "metrics.collect", PlanRisk.LOW, "authorized", "execute through sandbox", "verify result"),
    )
    audit = TaskAuditRecord("inspect repository and collect metrics", TaskIntent.INSPECT, ("step-1", "step-2"), True, "n9-safe-multi-step")
    return TaskPlan("inspect repository and collect metrics", TaskIntent.INSPECT, steps, PlanRisk.LOW, True, "safe multi-step plan", audit)


def test_n9_executes_a_multi_step_safe_plan_and_verifies_every_result(tmp_path: Path):
    plan = _safe_multi_step_plan()
    audit = tmp_path / "n9-execution.jsonl"
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT, Capability.METRICS],
        audit_path=audit,
        execution_id="n9-multi-step",
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.attempts == 2
    assert len(result.results) == 2
    assert all(item.success and item.verification_status == "verified" for item in result.results)
    assert verify_execution_audit(audit)


def test_n9_rejects_replay_of_an_interrupted_execution(tmp_path: Path):
    audit = tmp_path / "n9-recovery.jsonl"
    append_execution_record(audit, {"execution_id": "interrupted", "state": "running", "timestamp": "now"})
    result = recover_execution("interrupted", audit)
    assert result.state is ExecutionState.RECOVERY_REQUIRED
    assert "fresh authorization" in result.reason
