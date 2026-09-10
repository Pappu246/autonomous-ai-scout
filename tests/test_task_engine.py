from pathlib import Path

from autonomous_agent import task_engine
from autonomous_agent.sandbox import SandboxResult
from autonomous_agent.task_engine import TaskIntent, execute_task, plan_task


def test_test_plan_is_safe():
    plan = plan_task("run tests")
    assert plan.intent is TaskIntent.TEST
    assert plan.actions == ("inspect", "test")
    assert not plan.requires_approval


def test_execute_test_uses_centralized_sandbox(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run_safe_operation(operation, root, target=None):
        calls.append((operation, root, target))
        return SandboxResult(
            operation=operation,
            success=True,
            exit_status=0,
            output="pytest exit code: 0",
            output_truncated=False,
            command=("sandboxed", operation),
            verification_status="verified",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:01+00:00",
            network_disabled=True,
        )

    monkeypatch.setattr(task_engine, "run_safe_operation", fake_run_safe_operation)
    result = execute_task("run tests", tmp_path)
    assert result.status == "completed"
    assert [item[0] for item in calls] == ["inspect", "test"]
    assert all(item[1] == tmp_path for item in calls)


def test_write_request_remains_blocked(tmp_path: Path):
    result = execute_task("fix this bug", tmp_path)
    assert result.status == "approval_required"
    assert "WRITE/DEPLOY STEP BLOCKED" in result.output
