from pathlib import Path

from autonomous_agent.task_engine import TaskIntent, execute_task, plan_task


def test_test_plan_is_safe():
    plan = plan_task("run tests")
    assert plan.intent is TaskIntent.TEST
    assert plan.actions == ("inspect", "test")
    assert not plan.requires_approval


def test_write_request_remains_blocked(tmp_path: Path):
    result = execute_task("fix this bug", tmp_path)
    assert result.status == "approval_required"
    assert "WRITE/DEPLOY STEP BLOCKED" in result.output
