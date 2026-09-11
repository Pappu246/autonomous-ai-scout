from autonomous_agent.capability_policy import Capability
from autonomous_agent.tool_registry import authorize_tool, get_tool, list_tools


def test_registry_is_declarative_and_does_not_grant_access():
    assert get_tool("github.inspect") is not None
    decision = authorize_tool("github.inspect")
    assert not decision.allowed
    assert "explicitly granted" in decision.reason


def test_safe_tool_requires_only_explicit_safe_capability_grant():
    decision = authorize_tool("tests.run", [Capability.TEST])
    assert decision.allowed
    assert decision.capability == "test"


def test_write_tool_still_requires_approval():
    decision = authorize_tool("github.change", [Capability.SOURCE_WRITE])
    assert not decision.allowed
    assert "approval" in decision.reason
    approved = authorize_tool("github.change", [Capability.SOURCE_WRITE], explicitly_approved=True)
    assert not approved.allowed
    assert "permanently denied" in approved.reason


def test_merge_deploy_billing_and_destructive_tools_remain_denied():
    for name, capability in (
        ("github.merge", Capability.MERGE),
        ("production.deploy", Capability.DEPLOY),
        ("billing.manage", Capability.BILLING),
        ("destructive.execute", Capability.DESTRUCTIVE),
    ):
        decision = authorize_tool(name, [capability], explicitly_approved=True)
        assert not decision.allowed
        assert "permanently denied" in decision.reason


def test_unknown_tool_is_closed_by_default():
    decision = authorize_tool("unknown.tool", [])
    assert not decision.allowed
    assert "not registered" in decision.reason


def test_catalog_contains_no_duplicate_tool_names():
    tools = list_tools()
    assert len({tool.name for tool in tools}) == len(tools)
