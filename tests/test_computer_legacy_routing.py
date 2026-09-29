from autonomous_agent.task_intent import classify_intent
from autonomous_agent.task_plan_models import TaskIntent
from autonomous_agent.tool_router import DynamicToolRouter
from autonomous_agent.tool_registry import REGISTRY


def test_legacy_task_intent_classifies_computer_tasks():
    assert classify_intent("control the computer and complete this task") is TaskIntent.COMPUTER
    assert classify_intent("scroll down") is TaskIntent.COMPUTER


def test_legacy_tool_router_selects_native_computer_use():
    selection = DynamicToolRouter(REGISTRY).select_names(
        "control the computer and complete this task"
    )
    assert selection.tool_names == ("computer.use",)
    assert selection.candidate_names == ("computer.use",)
