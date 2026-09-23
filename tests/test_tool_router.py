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

def test_router_routes_canonical_workspace_shell_phrasing():
    selection = DynamicToolRouter().select_names(
        "Use the canonical local workspace shell to run exactly this command: pwd"
    )
    assert selection.tool_names == ("workspace.shell",)


def test_router_does_not_route_negated_shell_requests():
    selection = DynamicToolRouter().select_names(
        "inspect the workspace. Do not run a shell command or py_compile."
    )
    assert selection.tool_names == ("filesystem.list", "filesystem.read")


def test_router_defaults_ambiguous_workspace_requests_to_read_only_tools():
    selection = DynamicToolRouter().select_names(
        "inspect the repository workspace and investigate its structure"
    )
    assert selection.intent is TaskIntent.WORKSPACE
    assert selection.tool_names == ("filesystem.list", "filesystem.read")
    assert "filesystem.write" not in selection.tool_names
    assert "filesystem.transform" not in selection.tool_names


def test_router_keeps_explicit_workspace_mutation_requests_mutating_only_when_requested():
    selection = DynamicToolRouter().select_names("transform file config.py")
    assert selection.intent is TaskIntent.WORKSPACE
    assert selection.tool_names == ("filesystem.transform",)



def test_router_ignores_negated_workspace_mutations():
    selection = DynamicToolRouter().select_names(
        "inspect the repository workspace. Do not create, modify, save, or transform files."
    )
    assert selection.tool_names == ("filesystem.list", "filesystem.read")


def test_router_allows_explicit_workspace_mutation_in_positive_clause():
    selection = DynamicToolRouter().select_names(
        "inspect the repository. Transform file config.py."
    )
    assert selection.tool_names == ("filesystem.transform",)
