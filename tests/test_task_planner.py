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
    plan=plan_task("run tests",granted=[Capability.INSPECT,Capability.TEST]); assert plan.intent is TaskIntent.TEST and plan.executable; assert [s.tool_name for s in plan.steps]==["github.inspect","tests.run"]; assert plan.risk is PlanRisk.LOW and all(s.authorization=="authorized" for s in plan.steps)
def test_plan_fails_closed_without_capability_grant():
    plan=plan_task("run tests"); assert not plan.executable and "Authorization blocked" in plan.reason
def test_research_plan_uses_bounded_web_capability():
    plan=plan_task("research this topic",granted=[Capability.WEB_RESEARCH]); assert plan.executable; assert [s.tool_name for s in plan.steps]==["web.search","web.read","web.extract","web.compare"]
def test_change_plan_cannot_bypass_permanent_source_write_deny():
    plan=plan_task("fix the bug",granted=[Capability.INSPECT,Capability.TEST,Capability.SOURCE_WRITE],explicitly_approved=True); assert not plan.executable and "permanently denied" in plan.reason
def test_automation_without_registered_browser_tool_fails_closed():
    plan=plan_task("automate browser workflow"); assert not plan.executable and plan.steps==()
def test_custom_registry_is_the_only_tool_source():
    base=get_tool("tests.run"); registry=ToolRegistry((base,)); plan=plan_task("run tests",granted=[Capability.TEST],registry=registry); assert not plan.executable and "github.inspect" in plan.reason
def test_plan_is_deterministically_auditable():
    kwargs={"granted":[Capability.INSPECT,Capability.TEST]}; assert plan_task("run tests",**kwargs).audit==plan_task("run tests",**kwargs).audit
