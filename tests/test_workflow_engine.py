from autonomous_agent.capability_policy import Capability
from autonomous_agent.task_plan_models import PlanRisk, TaskIntent
from autonomous_agent.workflow_engine import WorkflowTask, build_workflow_plan


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
