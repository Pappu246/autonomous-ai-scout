from __future__ import annotations

from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import filesystem_connector, web_connector
from autonomous_agent.digital_tool import ToolInvocation, UniversalDigitalToolLayer


def test_discovery_returns_registered_tools_and_connector_metadata():
    layer = UniversalDigitalToolLayer(connectors=[web_connector()])
    candidates = layer.discover(category="web")
    names = {candidate.name for candidate in candidates}
    assert {"web.search", "web.read", "web.extract", "web.compare"} <= names
    assert any(candidate.name == "web.search" and candidate.connector_ids == ("web_research",) for candidate in candidates)


def test_discovery_can_filter_by_capability():
    layer = UniversalDigitalToolLayer()
    candidates = layer.discover(capability=Capability.FILES_WORKSPACE.value)
    assert {candidate.name for candidate in candidates} == {
        "filesystem.list", "filesystem.read", "filesystem.transform", "filesystem.write", "workspace.shell"
    }


def test_invocation_enforces_registered_arguments_and_authorization():
    layer = UniversalDigitalToolLayer()
    denied = layer.invoke(
        ToolInvocation("filesystem.write", {"path": "x", "content": "y", "unexpected": True}, "now"),
        granted=[Capability.FILES_WORKSPACE],
        invoker=lambda invocation: "should-not-run",
    )
    assert denied.success is False
    assert "unknown tool arguments" in denied.error


def test_invocation_enforces_approval_before_execution():
    layer = UniversalDigitalToolLayer()
    called = False

    def invoker(invocation):
        nonlocal called
        called = True
        return "ok"

    denied = layer.invoke(
        ToolInvocation("filesystem.write", {"path": "x", "content": "y"}, "now"),
        granted=[Capability.FILES_WORKSPACE],
        explicitly_approved=False,
        invoker=invoker,
    )
    assert not denied.success
    assert called is False
    assert "approval" in denied.error


def test_invocation_normalizes_successful_adapter_result():
    layer = UniversalDigitalToolLayer()
    result = layer.invoke(
        ToolInvocation("filesystem.read", {"path": "x"}, "now"),
        granted=[Capability.FILES_WORKSPACE],
        invoker=lambda invocation: {"path": invocation.arguments["path"], "ok": True},
    )
    assert result.success
    assert result.output == {"path": "x", "ok": True}


def test_connector_discovery_is_scoped_to_registered_connector_tools():
    fs_registry = filesystem_connector()
    layer = UniversalDigitalToolLayer(connectors=[fs_registry])
    read_candidates = layer.discover(query="filesystem.read")
    assert len(read_candidates) == 1
    assert read_candidates[0].connector_ids == ("filesystem_workspace_read",)


def test_missing_tool_fails_closed():
    layer = UniversalDigitalToolLayer()
    result = layer.invoke(ToolInvocation("not.a.real.tool", {}, "now"), invoker=lambda _: "bad")
    assert not result.success
    assert result.error == "tool is not registered"


def test_untrusted_origin_cannot_authorize_a_write_without_approval():
    from autonomous_agent.prompt_injection_guard import TrustLevel
    from autonomous_agent.digital_tool import ToolInvocation

    called = False

    def invoker(invocation):
        nonlocal called
        called = True
        return "ok"

    result = UniversalDigitalToolLayer().invoke(
        ToolInvocation("filesystem.write", {"path": "x", "content": "data"}, "now"),
        granted=[Capability.FILES_WORKSPACE],
        explicitly_approved=False,
        origin_trust=TrustLevel.TOOL_RESULT,
        invoker=invoker,
    )
    assert not result.success
    assert "untrusted content cannot authorize" in result.error
    assert called is False
