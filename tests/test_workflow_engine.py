from pathlib import Path

from autonomous_agent.browser_connector import ControlledBrowser, browser_transport_from_mapping
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.task_plan_models import PlanRisk, TaskIntent
from autonomous_agent.workflow_engine import WorkflowTask, build_workflow_plan, execute_workflow_plan


def test_workflow_orders_dependencies_and_builds_executable_plan():
    tasks = (
        WorkflowTask("summarize", "web.extract", Capability.WEB_RESEARCH, TaskIntent.RESEARCH, PlanRisk.LOW, ("research",)),
        WorkflowTask("research", "web.search", Capability.WEB_RESEARCH, TaskIntent.RESEARCH, PlanRisk.MEDIUM),
        WorkflowTask("draft", "email.draft", Capability.EMAIL, TaskIntent.EMAIL, PlanRisk.MEDIUM, ("summarize",)),
    )
    plan = build_workflow_plan(
        "daily research workflow",
        tasks,
        granted={Capability.WEB_RESEARCH, Capability.EMAIL},
    )
    assert plan.executable
    assert plan.intent is TaskIntent.AUTOMATE
    assert plan.risk is PlanRisk.MEDIUM
    assert [step.tool_name for step in plan.steps] == ["web.search", "web.extract", "email.draft"]
    assert plan.audit.authorized


def test_workflow_rejects_cycles_and_missing_capabilities():
    cyclic = (
        WorkflowTask("a", "web.search", Capability.WEB_RESEARCH, TaskIntent.RESEARCH, depends_on=("b",)),
        WorkflowTask("b", "web.read", Capability.WEB_RESEARCH, TaskIntent.RESEARCH, depends_on=("a",)),
    )
    plan = build_workflow_plan("cycle", cyclic, granted={Capability.WEB_RESEARCH})
    assert not plan.executable
    assert "cyclic" in plan.reason

    missing = (WorkflowTask("mail", "email.search", Capability.EMAIL, TaskIntent.EMAIL),)
    plan = build_workflow_plan("missing", missing, granted={Capability.WEB_RESEARCH})
    assert not plan.executable
    assert "not granted" in plan.reason


def test_workflow_executes_through_existing_safe_executor(tmp_path: Path):
    tasks = (
        WorkflowTask("open", "browser.open", Capability.BROWSER, TaskIntent.RESEARCH, PlanRisk.MEDIUM),
        WorkflowTask("extract", "browser.extract", Capability.BROWSER, TaskIntent.RESEARCH, PlanRisk.LOW, ("open",)),
    )
    plan = build_workflow_plan("browse workflow", tasks, granted={Capability.BROWSER})
    assert plan.executable
    browser = ControlledBrowser(
        {"example.com"},
        browser_transport_from_mapping({
            "open": {"status_code": 200, "title": "Example", "text": "opened"},
            "extract": {"status_code": 200, "title": "Example", "text": "facts"},
        }),
    )
    result = execute_workflow_plan(
        plan,
        tmp_path,
        granted={Capability.BROWSER},
        audit_path=tmp_path / "workflow.jsonl",
        execution_id="workflow-browser",
        browser_connector=browser,
        browser_requests={
            "browser.open": {"url": "https://example.com"},
            "browser.extract": {"url": "https://example.com", "fields": ["title"]},
        },
    )
    assert result.state is ExecutionState.VERIFIED
    assert len(result.results) == 2
    assert "opened" in result.results[0].output
    assert "facts" in result.results[1].output
