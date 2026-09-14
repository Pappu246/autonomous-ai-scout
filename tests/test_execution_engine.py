from pathlib import Path

from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_audit import append_execution_record, verify_execution_audit
from autonomous_agent.execution_engine import ExecutionState, execute_plan, recover_execution
from autonomous_agent.task_plan_models import TaskPlan
from autonomous_agent.task_planner import plan_task


def _inspect_plan(task: str = "inspect repository") -> TaskPlan:
    plan = plan_task(task, granted=[Capability.INSPECT, Capability.READ_FILE])
    assert plan.executable
    return TaskPlan(plan.task, plan.intent, (plan.steps[0],), plan.risk, True, plan.reason, plan.audit)


def test_safe_plan_executes_only_through_registered_sandbox(tmp_path: Path):
    plan = _inspect_plan()
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=tmp_path / "execution.jsonl",
        execution_id="safe-1",
        max_retries=1,
        timeout_seconds=30,
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.attempts == 1
    assert all(item.network_disabled for item in result.results)
    assert verify_execution_audit(tmp_path / "execution.jsonl")


def test_unknown_tool_fails_closed_before_execution(tmp_path: Path):
    plan = _inspect_plan()
    altered = type(plan)(plan.task, plan.intent, (type(plan.steps[0])(
        plan.steps[0].step_id, plan.steps[0].description, "unknown.tool", plan.steps[0].risk,
        "authorized", plan.steps[0].execution_boundary, plan.steps[0].verification),), plan.risk,
        True, plan.reason, plan.audit)
    result = execute_plan(altered, tmp_path, granted=[Capability.INSPECT], audit_path=tmp_path / "a.jsonl", execution_id="unknown")
    assert result.state is ExecutionState.BLOCKED
    assert "unknown tool" in result.reason


def test_unauthorized_capability_is_blocked(tmp_path: Path):
    plan = _inspect_plan()
    result = execute_plan(plan, tmp_path, granted=[], audit_path=tmp_path / "a.jsonl", execution_id="denied")
    assert result.state is ExecutionState.BLOCKED
    assert "authorization blocked" in result.reason


def test_high_risk_and_network_tools_cannot_cross_execution_boundary(tmp_path: Path):
    plan = plan_task("research this topic", granted=[Capability.NETWORK])
    assert not plan.executable
    result = execute_plan(plan, tmp_path, granted=[Capability.NETWORK], explicitly_approved=True, audit_path=tmp_path / "a.jsonl", execution_id="network")
    assert result.state is ExecutionState.BLOCKED


def test_approval_does_not_override_permanent_capability_denial(tmp_path: Path):
    plan = plan_task("fix the bug", granted=[Capability.INSPECT, Capability.TEST, Capability.SOURCE_WRITE], explicitly_approved=True)
    assert not plan.executable
    result = execute_plan(plan, tmp_path, granted=[Capability.INSPECT, Capability.TEST, Capability.SOURCE_WRITE], explicitly_approved=True, audit_path=tmp_path / "a.jsonl", execution_id="write")
    assert result.state is ExecutionState.BLOCKED


def test_audit_tampering_blocks_execution(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    plan = _inspect_plan()
    first = execute_plan(plan, tmp_path, granted=[Capability.INSPECT], audit_path=audit, execution_id="tamper")
    assert first.state is ExecutionState.VERIFIED
    audit.write_text(audit.read_text(encoding="utf-8").replace("verified", "tampered", 1), encoding="utf-8")
    assert not verify_execution_audit(audit)
    second = execute_plan(plan, tmp_path, granted=[Capability.INSPECT], audit_path=audit, execution_id="tamper-2")
    assert second.state is ExecutionState.BLOCKED


def test_execution_audit_does_not_store_raw_task_text(tmp_path: Path):
    secret_like = "inspect repository with credential=super-secret-value"
    audit = tmp_path / "audit.jsonl"
    plan = _inspect_plan(secret_like)
    result = execute_plan(plan, tmp_path, granted=[Capability.INSPECT], audit_path=audit, execution_id="privacy")
    assert result.state is ExecutionState.VERIFIED
    text = audit.read_text(encoding="utf-8")
    assert secret_like not in text
    assert "super-secret-value" not in text
    assert verify_execution_audit(audit)


def test_interrupted_execution_requires_fresh_authorization(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    append_execution_record(audit, {"execution_id": "interrupted", "state": "running", "timestamp": "now"})
    result = recover_execution("interrupted", audit)
    assert result.state is ExecutionState.RECOVERY_REQUIRED
    assert "fresh authorization" in result.reason
