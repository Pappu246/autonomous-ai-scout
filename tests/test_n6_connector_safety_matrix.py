from __future__ import annotations

from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import calendar_connector, filesystem_connector, gmail_connector, web_connector
from autonomous_agent.tool_registry import ApprovalRequirement, ToolRegistry


def test_n6_safe_connectors_bind_to_existing_registry_contract():
    registry = ToolRegistry()

    web = web_connector(registry).get("web_research")
    filesystem_read = filesystem_connector(registry).get("filesystem_workspace_read")
    filesystem_write = filesystem_connector(registry).get("filesystem_workspace_write")

    assert web is not None and web.enabled
    assert filesystem_read is not None and filesystem_read.enabled
    assert filesystem_write is not None and filesystem_write.enabled
    assert web.approval_requirement is ApprovalRequirement.NONE
    assert filesystem_read.approval_requirement is ApprovalRequirement.NONE
    assert filesystem_write.approval_requirement is ApprovalRequirement.HUMAN_REVIEW

    assert web_connector(registry).authorize("web_research", (Capability.WEB_RESEARCH,)).allowed
    assert filesystem_connector(registry).authorize(
        "filesystem_workspace_read", (Capability.FILES_WORKSPACE,)
    ).allowed
    assert not filesystem_connector(registry).authorize(
        "filesystem_workspace_write", (Capability.FILES_WORKSPACE,)
    ).allowed
    assert filesystem_connector(registry).authorize(
        "filesystem_workspace_write",
        (Capability.FILES_WORKSPACE,),
        explicitly_approved=True,
    ).allowed


def test_n6_authenticated_connectors_remain_disabled_by_default():
    registry = ToolRegistry()
    gmail = gmail_connector(registry, enabled=False).get("gmail")
    calendar = calendar_connector(registry, enabled=False).get("calendar")

    assert gmail is not None and not gmail.enabled
    assert calendar is not None and not calendar.enabled
    assert not gmail_connector(registry, enabled=False).authorize(
        "gmail", (Capability.EMAIL,), explicitly_approved=True
    ).allowed
    assert not calendar_connector(registry, enabled=False).authorize(
        "calendar", (Capability.CALENDAR,), explicitly_approved=True
    ).allowed


def test_n6_restricted_capabilities_remain_permanently_denied():
    registry = ToolRegistry()
    for tool_name, capability in (
        ("github.change", Capability.SOURCE_WRITE),
        ("github.merge", Capability.MERGE),
        ("production.deploy", Capability.DEPLOY),
        ("billing.manage", Capability.BILLING),
        ("secrets.manage", Capability.SECRETS),
        ("destructive.execute", Capability.DESTRUCTIVE),
    ):
        decision = registry.authorize(
            tool_name,
            (capability,),
            explicitly_approved=True,
        )
        assert not decision.allowed
        assert "permanently denied" in decision.reason
