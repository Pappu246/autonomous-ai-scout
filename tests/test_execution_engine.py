import json
import hashlib
from pathlib import Path

from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_audit import append_execution_record, verify_execution_audit
from autonomous_agent.execution_engine import ExecutionState, execute_plan, _authorization_digest, recover_execution
from autonomous_agent.sandbox import SandboxResult
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


def _verified_result(operation: str) -> SandboxResult:
    return SandboxResult(
        operation,
        True,
        0,
        "ok",
        False,
        (),
        "verified",
        "start",
        "finish",
        True,
    )


def test_execution_resumes_from_persisted_checkpoint_without_replaying_completed_step(tmp_path: Path, monkeypatch):
    plan = plan_task("run the tests", granted=[Capability.INSPECT, Capability.TEST])
    assert plan.executable
    audit = tmp_path / "execution.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    calls: list[str] = []

    def interrupt_on_second_tool(operation, root, target=None, **kwargs):
        calls.append(operation)
        if len(calls) == 2:
            raise KeyboardInterrupt("simulated process termination")
        return _verified_result(operation)

    monkeypatch.setattr("autonomous_agent.execution_engine.run_safe_operation", interrupt_on_second_tool)

    try:
        execute_plan(
            plan,
            tmp_path,
            granted=[Capability.INSPECT, Capability.TEST],
            audit_path=audit,
            checkpoint_path=checkpoint,
            execution_id="resume-1",
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("simulated interruption did not propagate")

    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["execution_id"] == "resume-1"
    assert saved["completed_step_ids"] == ["step-1"]
    assert plan.task not in checkpoint.read_text(encoding="utf-8")

    def finish_remaining(operation, root, target=None, **kwargs):
        calls.append(operation)
        return _verified_result(operation)

    monkeypatch.setattr("autonomous_agent.execution_engine.run_safe_operation", finish_remaining)
    resumed = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT, Capability.TEST],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="resume-1",
    )

    assert resumed.state is ExecutionState.VERIFIED
    assert calls == ["inspect", "test", "test"]
    final_state = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert final_state["state"] == "verified"
    assert final_state["completed_step_ids"] == ["step-1", "step-2"]


def test_checkpoint_identity_mismatch_fails_closed(tmp_path: Path):
    plan = plan_task("inspect repository", granted=[Capability.INSPECT])
    assert plan.executable
    audit = tmp_path / "execution.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="resume-identity",
    )
    altered = plan_task("inspect a different repository", granted=[Capability.INSPECT])
    result = execute_plan(
        altered,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="resume-identity",
    )
    assert result.state is ExecutionState.BLOCKED
    assert "does not match" in result.reason


def test_verified_checkpoint_prevents_duplicate_execution(tmp_path: Path, monkeypatch):
    plan = plan_task("inspect repository", granted=[Capability.INSPECT])
    assert plan.executable
    audit = tmp_path / "execution.jsonl"
    checkpoint = tmp_path / "checkpoint.json"

    first = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="resume-terminal",
    )
    assert first.state is ExecutionState.VERIFIED

    def should_not_run(*args, **kwargs):
        raise AssertionError("verified checkpoint must prevent duplicate execution")

    monkeypatch.setattr("autonomous_agent.execution_engine.run_safe_operation", should_not_run)
    second = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="resume-terminal",
    )
    assert second.state is ExecutionState.VERIFIED
    assert "already verified" in second.reason


def test_checkpoint_resume_requires_same_authorization_context(tmp_path: Path):
    plan = _inspect_plan()
    audit = tmp_path / "authorization.jsonl"
    checkpoint = tmp_path / "authorization.checkpoint.json"
    first = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="auth-binding",
    )
    assert first.state is ExecutionState.VERIFIED

    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["state"] = "running"
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    changed_plan = plan
    result = execute_plan(
        changed_plan,
        tmp_path,
        granted=[Capability.INSPECT, Capability.READ_FILE],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="auth-binding",
    )
    assert result.state is ExecutionState.BLOCKED
    assert "does not match" in result.reason



def test_verified_audit_step_is_recoverable_from_audit(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    append_execution_record(audit, {"execution_id": "audit-recover", "event": "tool_result", "step_id": "step-1", "result": "success", "verification": "verified"})
    from autonomous_agent.execution_engine import _verified_steps_from_audit
    assert _verified_steps_from_audit(audit, "audit-recover") == {"step-1"}


def test_workspace_shell_uses_dedicated_sandbox_operation(tmp_path: Path):
    plan = plan_task("run a shell command", granted=[Capability.WORKSPACE_SHELL])
    assert plan.executable
    from dataclasses import dataclass
    @dataclass
    class Shell:
        def run(self, argv, *, timeout_seconds=20):
            return type("R", (), {
                "success": True,
                "argv": tuple(argv),
                "output": "safe-shell-ok",
                "exit_status": 0,
                "reason": "verified",
            })()
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.WORKSPACE_SHELL],
        audit_path=tmp_path / "shell.jsonl",
        execution_id="shell-1",
        workspace_connector=Shell(),
        workspace_request={"workspace.shell": {"argv": ["pwd"]}},
    )
    assert result.state is ExecutionState.VERIFIED
    assert result.results[-1].operation == "workspace_shell"
    assert "safe-shell-ok" in result.results[-1].output


def test_canonical_executor_enforces_consequence_policy(tmp_path: Path):
    base = plan_task("inspect repository", granted=[Capability.INSPECT])
    step = base.steps[0]
    changed = type(step)(
        step.step_id,
        step.description,
        "filesystem.write",
        step.risk,
        "authorized",
        step.execution_boundary,
        step.verification,
    )
    altered = TaskPlan(base.task, base.intent, (changed,), base.risk, True, base.reason, base.audit)
    result = execute_plan(
        altered,
        tmp_path,
        granted=[Capability.FILES_WORKSPACE],
        explicitly_approved=False,
        audit_path=tmp_path / "policy.jsonl",
        execution_id="policy-1",
    )
    assert result.state is ExecutionState.BLOCKED
    assert "consequence-aware policy" in result.reason


def test_failed_checkpoint_requires_recovery_instead_of_replay(tmp_path: Path):
    plan = _inspect_plan()
    audit = tmp_path / "failed.jsonl"
    checkpoint = tmp_path / "failed.checkpoint.json"
    execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="failed-replay",
    )
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["state"] = "failed"
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="failed-replay",
    )
    assert result.state is ExecutionState.RECOVERY_REQUIRED
    assert "automatic replay" in result.reason


def test_forged_verified_checkpoint_cannot_claim_completion_without_audit(tmp_path: Path):
    plan = _inspect_plan()
    audit = tmp_path / "forged.jsonl"
    checkpoint = tmp_path / "forged.checkpoint.json"
    checkpoint.write_text(json.dumps({
        "schema_version": 2,
        "execution_id": "forged",
        "task_digest": hashlib.sha256(plan.task.encode()).hexdigest(),
        "plan_digest": plan.audit.plan_digest,
        "authorization_digest": _authorization_digest([Capability.INSPECT], False, plan),
        "state": "verified",
        "completed_step_ids": [step.step_id for step in plan.steps],
        "total_attempts": 1,
        "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    from autonomous_agent.execution_engine import execute_plan
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="forged",
    )
    assert result.state is ExecutionState.RECOVERY_REQUIRED
    assert "trusted audit" in result.reason


def test_checkpoint_rejects_non_list_completed_steps(tmp_path: Path):
    from autonomous_agent.execution_checkpoint import ExecutionCheckpointStore
    checkpoint = tmp_path / "bad.json"
    checkpoint.write_text(json.dumps({
        "schema_version": 2,
        "execution_id": "bad",
        "task_digest": "task",
        "plan_digest": "plan",
        "authorization_digest": "auth",
        "state": "running",
        "completed_step_ids": "step-1",
        "total_attempts": 0,
        "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    try:
        ExecutionCheckpointStore(checkpoint).load()
    except ValueError as exc:
        assert "must be a list" in str(exc)
    else:
        raise AssertionError("malformed completed_step_ids must be rejected")


def test_checkpoint_rejects_unknown_state(tmp_path: Path):
    from autonomous_agent.execution_checkpoint import ExecutionCheckpointStore
    with __import__("pytest").raises(ValueError, match="state is invalid"):
        ExecutionCheckpointStore(tmp_path / "new.json").save(
            execution_id="bad",
            task_digest="task",
            plan_digest="plan",
            authorization_digest="auth",
            state="unknown",
            completed_step_ids=(),
            total_attempts=0,
        )


def test_resume_does_not_trust_checkpoint_completed_steps_without_audit(tmp_path: Path):
    plan = _inspect_plan()
    audit = tmp_path / "resume-forged.jsonl"
    checkpoint = tmp_path / "resume-forged.checkpoint.json"
    checkpoint.write_text(json.dumps({
        "schema_version": 2,
        "execution_id": "resume-forged",
        "task_digest": hashlib.sha256(plan.task.encode()).hexdigest(),
        "plan_digest": plan.audit.plan_digest,
        "authorization_digest": _authorization_digest([Capability.INSPECT], False, plan),
        "state": "running",
        "completed_step_ids": [step.step_id for step in plan.steps],
        "total_attempts": 1,
        "updated_at": "2026-09-23T00:00:00+00:00",
    }), encoding="utf-8")
    result = execute_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=audit,
        checkpoint_path=checkpoint,
        execution_id="resume-forged",
    )
    assert result.state is ExecutionState.RECOVERY_REQUIRED
    assert "without matching trusted audit evidence" in result.reason
