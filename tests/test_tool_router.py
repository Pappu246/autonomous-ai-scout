from autonomous_agent.capability_policy import Capability
from autonomous_agent.task_intent import TaskIntent
from autonomous_agent.tool_registry import ToolRegistry, get_tool
from autonomous_agent.tool_router import DynamicToolRouter


def test_router_selects_narrow_web_read_for_explicit_url():
    selection = DynamicToolRouter().select_names("read this page: https://example.com")
    assert selection.intent is TaskIntent.RESEARCH
    assert selection.tool_names == ("web.read",)


def test_router_selects_email_draft_without_selecting_send():
    selection = DynamicToolRouter().select_names("draft an email to the team")
    assert selection.intent is TaskIntent.EMAIL
    assert selection.tool_names == ("email.draft",)


def test_router_selects_workspace_list_for_directory_request():
    selection = DynamicToolRouter().select_names("list files in the workspace")
    assert selection.intent is TaskIntent.WORKSPACE
    assert selection.tool_names == ("filesystem.list",)


def test_router_selects_browser_tool_for_safe_automation_request():
    selection = DynamicToolRouter().select_names("automate browser navigation")
    assert selection.intent is TaskIntent.AUTOMATE
    assert selection.tool_names == ("browser.open",)


def test_router_keeps_generic_automation_fail_closed():
    selection = DynamicToolRouter().select_names("automate the deployment")
    assert selection.intent is TaskIntent.AUTOMATE
    assert selection.tool_names == ()
    assert selection.missing_tools == ()


def test_router_reports_missing_required_tool_from_custom_registry():
    registry = ToolRegistry((get_tool("tests.run"),))
    selection = DynamicToolRouter(registry).select_names("run tests")
    assert selection.candidate_names == ("github.inspect", "tests.run")
    assert selection.tool_names == ("tests.run",)
    assert selection.missing_tools == ("github.inspect",)


def test_router_does_not_expand_capabilities():
    selection = DynamicToolRouter().select("send email")
    assert selection == ("email.send",)
    assert Capability.EMAIL.value == get_tool("email.send").capability

def test_negative_capability_constraints_do_not_override_workspace_read():
    task = (
        "Read file docs/README.md in the isolated workspace and summarize two facts. "
        "Do not change any file. Do not execute shell commands. "
        "Do not use network, browser, GitHub, email, calendar, applications, or computer control."
    )
    selection = DynamicToolRouter().select_names(task)
    assert selection.intent is TaskIntent.WORKSPACE
    assert selection.tool_names == ("filesystem.read",)



def test_read_only_project_status_does_not_select_mutating_workspace_tools():
    task = "Inspect the current project status and provide a brief read-only summary of the project's structure, recent status, and any visible issues. Do not edit or delete files, execute commands, access external accounts, send messages, or perform any action that changes data. If information is unavailable, clearly report that."
    selection = DynamicToolRouter().select_names(task)
    assert selection.intent is TaskIntent.INSPECT
    assert selection.tool_names == ("github.inspect",)
    assert "filesystem.transform" not in selection.tool_names
    assert "filesystem.write" not in selection.tool_names


def test_negative_lint_instruction_does_not_change_test_routing_or_grants():
    from autonomous_agent.task_planner import candidate_tool_names, default_grants_for_task, plan_task

    task = "Run tests. Do not run lint."
    assert candidate_tool_names(task) == ("github.inspect", "tests.run")
    assert default_grants_for_task(task) == (Capability.INSPECT, Capability.TEST)
    plan = plan_task(task, granted=default_grants_for_task(task))
    assert plan.executable
    assert tuple(step.tool_name for step in plan.steps) == ("github.inspect", "tests.run")
    assert "lint.run" not in tuple(step.tool_name for step in plan.steps)


def test_negative_test_instruction_does_not_change_lint_routing_or_grants():
    from autonomous_agent.task_planner import candidate_tool_names, default_grants_for_task

    task = "Run lint. Do not run tests."
    assert candidate_tool_names(task) == ("github.inspect", "lint.run")
    assert default_grants_for_task(task) == (Capability.INSPECT, Capability.LINT)


def test_compound_negative_instruction_does_not_select_forbidden_capability():
    from autonomous_agent.task_planner import candidate_tool_names

    assert candidate_tool_names("Inspect repository and run tests. Do not run lint.") == (
        "github.inspect", "tests.run",
    )
    assert candidate_tool_names("Inspect repository and run lint. Do not run tests.") == (
        "github.inspect", "lint.run",
    )
