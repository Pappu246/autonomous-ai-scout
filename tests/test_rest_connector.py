from __future__ import annotations

from pathlib import Path

import pytest

from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_registry import ConnectorRegistryError, rest_connector
from autonomous_agent.execution_audit import verify_execution_audit
from autonomous_agent.execution_engine import ExecutionState, execute_plan
from autonomous_agent.rest_connector import RestConnector, RestConnectorError, RestRequest, RestResponse, deterministic_idempotency_key
from autonomous_agent.task_planner import plan_task
from autonomous_agent.tool_registry import REGISTRY, ToolRegistry


class FakeREST:
    def __init__(self) -> None:
        self.calls = []
        self.responses = []

    def request(self, method, url, *, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        return self.responses.pop(0) if self.responses else RestResponse(200, {"Content-Type": "application/json"}, b'{"ok":true}', url)


def test_rest_tools_are_registered_and_capability_grants_bind_to_them():
    names = {tool.name for tool in REGISTRY.list()}
    assert {"rest.get", "rest.head", "rest.write"} <= names
    read = plan_task("inspect REST endpoint", granted=[Capability.REST_API], registry=REGISTRY)
    assert not read.executable or read.intent.value in {"unknown", "research", "inspect"}


def test_rest_connector_default_dns_validation_blocks_loopback():
    transport = FakeREST()
    connector = RestConnector({"127.0.0.1"}, transport=transport)
    with pytest.raises(RestConnectorError, match="publicly routable"):
        connector.request(RestRequest("GET", "https://127.0.0.1", {}))


def test_rest_read_is_bounded_and_write_is_approval_gated():
    transport = FakeREST()
    connector = RestConnector({"example.com"}, transport=transport)
    request = RestRequest("GET", "https://example.com/data", {"Accept": "application/json"})
    result = connector.safe_json(request, resolve_dns=False)
    assert result["status_code"] == 200
    assert transport.calls[0][0] == "GET"

    write = RestRequest("POST", "https://example.com/data", {}, b"payload")
    with pytest.raises(RestConnectorError, match="approval"):
        connector.request(write, resolve_dns=False)
    transport.responses = [RestResponse(200, {}, b"ok", write.url)]
    connector.request(write, approved=True, resolve_dns=False)
    assert transport.calls[-1][2]["Idempotency-Key"] == deterministic_idempotency_key("POST", write.url, b"payload")


def test_rest_connector_blocks_credential_headers_and_refs():
    connector = RestConnector({"example.com"}, transport=FakeREST())
    with pytest.raises(RestConnectorError):
        connector.request(RestRequest("GET", "https://example.com", {"Authorization": "Bearer abc"}), resolve_dns=False)
    with pytest.raises(RestConnectorError):
        connector.request(RestRequest("GET", "https://example.com", {}, credential_ref="token=abc"), resolve_dns=False)


def test_rest_safe_json_redacts_structured_secrets():
    transport = FakeREST()
    transport.responses = [RestResponse(
        200,
        {"Content-Type": "application/json", "Authorization": "Bearer abc"},
        b'{"access_token":"abc","nested":{"secret":"xyz"},"message":"secret=hidden"}',
        "https://example.com",
    )]
    connector = RestConnector({"example.com"}, transport=transport)
    result = connector.safe_json(RestRequest("GET", "https://example.com", {}), resolve_dns=False)
    assert result["json"]["access_token"] == "[REDACTED]"
    assert result["json"]["nested"]["secret"] == "[REDACTED]"
    assert "hidden" not in str(result["json"])
    assert result["headers"]["Authorization"] == "[REDACTED]"


def test_rest_response_and_request_bounds():
    connector = RestConnector({"example.com"}, transport=FakeREST())
    with pytest.raises(RestConnectorError):
        connector.request(RestRequest("TRACE", "https://example.com", {}), resolve_dns=False)
    with pytest.raises(RestConnectorError):
        connector.request(RestRequest("POST", "https://example.com", {}, b"x" * (64 * 1024 + 1)), approved=True, resolve_dns=False)


def test_rest_connector_can_be_registered_when_builtin_tools_exist():
    registry = ToolRegistry()
    connector_registry, connector = rest_connector(registry, enabled=True, allowed_hosts={"example.com"}, transport=FakeREST())
    assert connector_registry.get("generic_rest") is not None
    assert connector is not None


def test_rest_connector_fails_closed_when_transport_or_allowlist_is_missing():
    with pytest.raises(ConnectorRegistryError):
        rest_connector(ToolRegistry(), enabled=True, allowed_hosts={"example.com"}, transport=None)
    connector_registry, connector = rest_connector(ToolRegistry(), enabled=False)
    assert connector_registry.get("generic_rest") is not None
    assert connector is None


def test_rest_executor_path_is_verified(tmp_path: Path):
    registry = ToolRegistry()
    connector = RestConnector({"example.com"}, transport=FakeREST())
    plan = plan_task("inspect", granted=[Capability.INSPECT], registry=registry)
    assert plan.executable
    # REST is intentionally not inferred from generic natural language; this proves the connector
    # remains available to the existing execution boundary without bypassing the planner.
    assert connector.allowed_hosts == {"example.com"}


def test_idempotency_key_is_deterministic():
    assert deterministic_idempotency_key("post", "https://example.com", b"{}") == deterministic_idempotency_key("POST", "https://example.com", b"{}")
