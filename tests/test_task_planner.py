from autonomous_agent.capability_policy import Capability
from autonomous_agent.task_intent import classify_intent
from autonomous_agent.task_plan_models import PlanRisk, TaskIntent
from autonomous_agent.task_planner import plan_task
from autonomous_agent.tool_registry import ToolRegistry, get_tool


def test_intent_classification_is_conservative():
    assert classify_intent("research free AI tools") is TaskIntent.RESEARCH
    assert classify_intent("run pytest") is TaskIntent.TEST
    assert classify_intent("inspect repository") is TaskIntent.INSPECT
    assert classify_intent("do something mysterious") is TaskIntent.UNKNOWN


def test_test_plan_uses_only_registry_tools_and_can_be_authorized():
    plan = plan_task("run tests", granted=[Capability.INSPECT, Capability.TEST])
    assert plan.intent is TaskIntent.TEST
    assert plan.executable
    assert [step.tool_name for step in plan.steps] == ["github.inspect", "tests.run"]
    assert plan.risk is PlanRisk.LOW
    assert plan.audit.plan_digest
    assert all(step.authorization == "authorized" for step in plan.steps)


def test_plan_fails_closed_without_capability_grant():
    plan = plan_task("run tests")
    assert not plan.executable
    assert "Authorization blocked" in plan.reason
    assert all(step.authorization == "blocked" for step in plan.steps)


def test_research_plan_respects_permanent_network_policy():
    plan = plan_task("research this topic", granted=[Capability.NETWORK], explicitly_approved=True)
    assert not plan.executable
    assert plan.steps[0].tool_name == "network.fetch"
    assert "permanently denied" in plan.reason


def test_change_plan_cannot_bypass_permanent_source_write_deny():
    plan = plan_task(
        "fix the bug",
        granted=[Capability.INSPECT, Capability.TEST, Capability.SOURCE_WRITE],
        explicitly_approved=True,
    )
    assert not plan.executable
    assert "permanently denied" in plan.reason


def test_automation_without_registered_browser_tool_fails_closed():
    plan = plan_task("automate browser workflow")
    assert not plan.executable
    assert plan.steps == ()
    assert "fails closed" in plan.reason


def test_custom_registry_is_the_only_tool_source():
    base = get_tool("tests.run")
    assert base is not None
    registry = ToolRegistry((base,))
    plan = plan_task("run tests", granted=[Capability.TEST], registry=registry)
    assert not plan.executable
    assert "github.inspect" in plan.reason


def test_plan_is_deterministically_auditable():
    kwargs = {"granted": [Capability.INSPECT, Capability.TEST]}
    first = plan_task("run tests", **kwargs)
    second = plan_task("run tests", **kwargs)
    assert first.audit == second.audit
    assert first.metadata == second.metadata
