import json
from pathlib import Path

from autonomous_agent.adaptive_execution import execute_adaptive_plan
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.sandbox import SandboxResult
from autonomous_agent.task_plan_models import TaskStep
from autonomous_agent.task_planner import plan_task


def _result(operation: str, *, success: bool) -> SandboxResult:
    return SandboxResult(
        operation,
        success,
        0 if success else 1,
        "secret-output" if not success else "verified-output",
        False,
        (),
        "verified" if success else "failed",
        "start",
        "finish",
        True,
    )


def test_observe_retry_replan_and_verify(monkeypatch, tmp_path: Path):
    plan = plan_task(
        "research this topic",
        granted=[Capability.WEB_RESEARCH, Capability.BROWSER],
    )
    assert plan.executable
    calls: list[str] = []

    def fake_safe_operation(operation, *args, **kwargs):
        calls.append(operation)
        return _result(operation, success=len(calls) > 2)

    monkeypatch.setattr("autonomous_agent.execution_engine.run_safe_operation", fake_safe_operation)

    def replanner(observation, remaining):
        assert observation.tool_name == "web.search"
        return plan_task(
            "browser navigation to https://example.com",
            granted=[Capability.BROWSER],
        ).steps[:1]

    audit = tmp_path / "adaptive.jsonl"
    result = execute_adaptive_plan(
        plan,
        tmp_path,
        granted=[Capability.WEB_RESEARCH, Capability.BROWSER],
        audit_path=audit,
        execution_id="adaptive-1",
        max_retries_per_step=1,
        max_replans=1,
        replanner=replanner,
    )

    assert result.state is ExecutionState.VERIFIED
    assert result.replans == 1
    assert calls[0:2] == ["web_research", "web_research"]
    assert "browser" in calls
    assert [item.outcome for item in result.observations[:2]] == ["failed", "failed"]
    assert any(item.tool_name == "browser.open" and item.success for item in result.observations)
    audit_text = audit.read_text(encoding="utf-8")
    assert "OBSERVATION" in audit_text
    assert "RETRY" in audit_text
    assert "REPLAN" in audit_text
    assert "VERIFICATION" in audit_text
    assert "TASK_COMPLETED" in audit_text
    assert "secret-output" not in audit_text


def test_policy_blocked_step_never_replans(tmp_path: Path):
    plan = plan_task("run tests")
    called = False

    def replanner(observation, remaining):
        nonlocal called
        called = True
        return (
            TaskStep(
                "replacement",
                "replacement",
                "github.inspect",
                plan.risk,
                "authorized",
                "existing boundary",
                "verify",
            ),
        )

    result = execute_adaptive_plan(
        plan,
        tmp_path,
        granted=[],
        audit_path=tmp_path / "blocked.jsonl",
        execution_id="adaptive-blocked",
        replanner=replanner,
    )

    assert result.state is ExecutionState.BLOCKED
    assert result.replans == 0
    assert called is False


def test_failed_recovery_without_replanner_is_explicit(tmp_path: Path, monkeypatch):
    plan = plan_task("inspect repository", granted=[Capability.INSPECT])
    assert plan.executable

    monkeypatch.setattr(
        "autonomous_agent.execution_engine.run_safe_operation",
        lambda operation, *args, **kwargs: _result(operation, success=False),
    )

    result = execute_adaptive_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=tmp_path / "failed.jsonl",
        execution_id="adaptive-failed",
        max_retries_per_step=1,
        max_replans=0,
    )

    assert result.state is ExecutionState.FAILED
    assert result.replans == 0
    assert result.attempts == 2


def test_replanner_rejects_duplicate_replacement_ids(tmp_path: Path, monkeypatch):
    plan = plan_task("inspect repository", granted=[Capability.INSPECT])
    monkeypatch.setattr(
        "autonomous_agent.execution_engine.run_safe_operation",
        lambda operation, *args, **kwargs: _result(operation, success=False),
    )

    result = execute_adaptive_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=tmp_path / "duplicate.jsonl",
        execution_id="adaptive-duplicate",
        replanner=lambda observation, remaining: (plan.steps[0], plan.steps[0]),
    )
    assert result.state is ExecutionState.FAILED
    assert "duplicate replacement step ids" in result.reason


def test_replanner_rejects_oversized_replacement(tmp_path: Path, monkeypatch):
    plan = plan_task("inspect repository", granted=[Capability.INSPECT])
    monkeypatch.setattr(
        "autonomous_agent.execution_engine.run_safe_operation",
        lambda operation, *args, **kwargs: _result(operation, success=False),
    )
    replacement = tuple(
        TaskStep(f"replacement-{index}", "replacement", "github.inspect", plan.risk, "authorized", "existing boundary", "verify")
        for index in range(13)
    )
    result = execute_adaptive_plan(
        plan,
        tmp_path,
        granted=[Capability.INSPECT],
        audit_path=tmp_path / "oversized.jsonl",
        execution_id="adaptive-oversized",
        replanner=lambda observation, remaining: replacement,
    )
    assert result.state is ExecutionState.FAILED
    assert "bounded step limit" in result.reason
