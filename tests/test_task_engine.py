from pathlib import Path

from autonomous_agent.task_engine import TaskIntent, execute_task, plan_task


def test_test_plan_is_safe():
    plan = plan_task("run tests")
    assert plan.intent is TaskIntent.TEST
    assert plan.actions == ("inspect", "test")
    assert not plan.requires_approval


def test_execute_test_uses_sandbox(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
    result = execute_task("run tests", tmp_path)
    assert result.status == "completed"
    assert "pytest exit code: 0" in result.output


def test_write_request_remains_blocked(tmp_path: Path):
    result = execute_task("fix this bug", tmp_path)
    assert result.status == "approval_required"
    assert "WRITE/DEPLOY STEP BLOCKED" in result.output
