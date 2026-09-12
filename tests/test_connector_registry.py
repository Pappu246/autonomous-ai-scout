from autonomous_agent.connector_registry import ConnectorRegistryError, rest_connector
from autonomous_agent.tool_registry import ToolRegistry


def test_rest_connector_registers_tools_in_custom_registry_and_stays_disabled():
    registry = ToolRegistry()
    connectors, connector = rest_connector(registry, enabled=False)
    assert connector is None
    assert connectors.get("generic_rest") is not None
    assert connectors.get("generic_rest").enabled is False
    assert all(registry.get(name) is not None for name in ("rest.get", "rest.head", "rest.write"))


def test_rest_connector_with_hosts_requires_injected_transport():
    registry = ToolRegistry()
    try:
        rest_connector(registry, allowed_hosts={"api.example.com"})
    except ConnectorRegistryError as exc:
        assert "injected transport" in str(exc)
    else:
        raise AssertionError("REST connector unexpectedly accepted a missing transport")
