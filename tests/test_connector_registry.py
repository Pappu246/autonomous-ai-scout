import pytest

from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import ConnectorRegistry, ConnectorRegistryError, ConnectorSpec, ConnectorAuth, authorize_connector, get_connector
from autonomous_agent.tool_registry import REGISTRY


def test_builtin_connectors_use_existing_registered_tools_only():
    github = get_connector("github")
    assert github is not None
    assert github.registered_tools == ("github.inspect",)
    assert all(REGISTRY.get(name) is not None for name in github.registered_tools)


def test_connector_authorization_delegates_to_tool_registry():
    allowed = authorize_connector("github", granted=[Capability.INSPECT])
    assert allowed.allowed
    denied = authorize_connector("github", granted=[])
    assert not denied.allowed


def test_network_connector_cannot_bypass_existing_approval():
    denied = authorize_connector("web_research", granted=[Capability.NETWORK])
    assert not denied.allowed
    allowed = authorize_connector("web_research", granted=[Capability.NETWORK], explicitly_approved=True)
    assert allowed.allowed


def test_future_connectors_fail_closed_without_registered_tools():
    for identity in ("email", "calendar_api", "browser"):
        decision = authorize_connector(identity, granted=[Capability.NETWORK], explicitly_approved=True)
        assert not decision.allowed
        assert "registered executable tool" in decision.reason or "disabled" in decision.reason


def test_unknown_connector_fails_closed():
    decision = authorize_connector("unknown", granted=[Capability.INSPECT])
    assert not decision.allowed


def test_connector_registry_rejects_unknown_tool_reference():
    registry = ConnectorRegistry([])
    with pytest.raises(ConnectorRegistryError):
        registry.register(ConnectorSpec(
            "bad", ("test",), ("scope",), ConnectorAuth.NONE, False, "low", "read_only", False, True, True, ("unknown.tool",)
        ))


def test_connector_metadata_does_not_create_new_permission_state():
    connector = get_connector("project_repository")
    assert connector is not None
    assert connector.approval_required
    assert connector.sandbox_required
    assert connector.audit_required
    assert connector.registered_tools == ("github.inspect", "github.change")
