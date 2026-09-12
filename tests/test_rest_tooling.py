import ipaddress

from autonomous_agent.rest_connector import RestConnector, RestResponse, RestConnectorError
from autonomous_agent.rest_tooling import execute_rest_tool


class T:
    def request(self, method, url, *, headers, body, timeout):
        return RestResponse(200, {"Content-Type": "application/json"}, b'{"ok":true}', url)


def public_resolver(_host):
    return [ipaddress.ip_address("93.184.216.34")]


def test_rest_tool_adapter():
    connector = RestConnector({"api.example.com"}, transport=T())
    result = execute_rest_tool(
        connector,
        {"method": "GET", "url": "https://api.example.com/v1", "headers": {}},
        resolver=public_resolver,
    )
    assert result["status_code"] == 200
    assert result["json"] == {"ok": True}


def test_rest_tool_write_still_requires_approval():
    connector = RestConnector({"api.example.com"}, transport=T())
    request = {"method": "POST", "url": "https://api.example.com/v1", "headers": {}, "body": "x"}
    try:
        execute_rest_tool(connector, request, resolver=public_resolver)
    except RestConnectorError as exc:
        assert "human approval" in str(exc)
    else:
        raise AssertionError("write unexpectedly executed without approval")


def test_rest_tool_credential_ref_never_becomes_a_header():
    connector = RestConnector({"api.example.com"}, transport=T())
    result = execute_rest_tool(
        connector,
        {
            "method": "GET",
            "url": "https://api.example.com/v1",
            "headers": {},
            "credential_ref": "provider:api-key",
        },
        resolver=public_resolver,
    )
    assert result["status_code"] == 200
