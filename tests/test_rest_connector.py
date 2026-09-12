from __future__ import annotations

from dataclasses import dataclass
import ipaddress

import pytest

from autonomous_agent.rest_connector import (
    RestConnector,
    RestConnectorError,
    RestRequest,
    RestResponse,
    deterministic_idempotency_key,
    validate_headers,
    validate_public_https_url,
)


@dataclass
class FakeTransport:
    responses: list[RestResponse]
    calls: list[tuple]

    def request(self, method, url, *, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        if not self.responses:
            raise AssertionError("unexpected transport call")
        return self.responses.pop(0)


def response(status=200, body=b'{"ok":true}', url="https://api.example.com/v1"):
    return RestResponse(status, {"Content-Type": "application/json"}, body, url)


def public_resolver(_host):
    return [ipaddress.ip_address("93.184.216.34")]


def private_resolver(_host):
    return [ipaddress.ip_address("10.0.0.1"), ipaddress.ip_address("2001:db8::1")]


def connector(*responses):
    transport = FakeTransport(list(responses), [])
    return RestConnector({"api.example.com"}, transport=transport), transport


def test_url_requires_https_and_allowlist():
    with pytest.raises(RestConnectorError):
        validate_public_https_url("http://api.example.com/v1", frozenset({"api.example.com"}), resolver=public_resolver)
    with pytest.raises(RestConnectorError):
        validate_public_https_url("https://other.example.com/v1", frozenset({"api.example.com"}), resolver=public_resolver)
    with pytest.raises(RestConnectorError):
        validate_public_https_url("https://user:pass@api.example.com/v1", frozenset({"api.example.com"}), resolver=public_resolver)


def test_url_blocks_non_public_literal_ipv4_and_ipv6():
    allowed = frozenset({"127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "fe80::1"})
    for url in (
        "https://127.0.0.1/",
        "https://10.0.0.1/",
        "https://169.254.169.254/",
        "https://[::1]/",
        "https://[fc00::1]/",
        "https://[fe80::1]/",
    ):
        with pytest.raises(RestConnectorError):
            validate_public_https_url(url, allowed)


def test_dns_resolution_is_mandatory_and_checks_all_addresses():
    allowed = frozenset({"api.example.com"})
    with pytest.raises(RestConnectorError, match="not publicly routable"):
        validate_public_https_url("https://api.example.com/v1", allowed, resolver=private_resolver)
    assert validate_public_https_url("https://api.example.com/v1", allowed, resolver=public_resolver).startswith("https://")


def test_read_succeeds_and_redacts_text_and_json_secrets():
    api, transport = connector(response(body=b'{"token":"Bearer secret-value", "nested":{"api_key":"secret-value"}}'))
    result = api.safe_json(RestRequest("GET", "https://api.example.com/v1", {"Accept": "application/json"}), resolver=public_resolver)
    assert result["status_code"] == 200
    assert "secret-value" not in result["body"]
    assert result["json"]["token"] == "Bearer [REDACTED]"
    assert result["json"]["nested"]["api_key"] == "[REDACTED]"
    assert transport.calls[0][0] == "GET"


def test_write_requires_human_approval_and_adds_idempotency():
    api, transport = connector(response())
    request = RestRequest("POST", "https://api.example.com/v1", {"Content-Type": "application/json"}, b'{"x":1}')
    with pytest.raises(RestConnectorError, match="human approval"):
        api.request(request, resolver=public_resolver)
    result = api.request(request, approved=True, resolver=public_resolver)
    expected = deterministic_idempotency_key("POST", request.url, request.body)
    assert result.status_code == 200
    assert transport.calls[-1][2]["Idempotency-Key"] == expected


def test_safe_methods_retry_but_writes_do_not():
    api, transport = connector(response(503), response(200))
    assert api.request(RestRequest("GET", "https://api.example.com/v1", {}), resolver=public_resolver).status_code == 200
    assert len(transport.calls) == 2

    api, transport = connector(response(503), response(200))
    assert api.request(RestRequest("POST", "https://api.example.com/v1", {}, b"x"), approved=True, resolver=public_resolver).status_code == 503
    assert len(transport.calls) == 1


def test_redirect_must_remain_allowlisted_https_and_revalidate_dns():
    api, _ = connector(RestResponse(302, {"Location": "https://evil.example.com/x"}, b"", "https://api.example.com/v1"))
    with pytest.raises(RestConnectorError, match="allowlisted"):
        api.request(RestRequest("GET", "https://api.example.com/v1", {}), resolver=public_resolver)

    api, transport = connector(
        RestResponse(302, {"Location": "https://api.example.com/next"}, b"", "https://api.example.com/v1"),
        response(),
    )
    assert api.request(RestRequest("GET", "https://api.example.com/v1", {}), resolver=public_resolver).status_code == 200
    assert transport.calls[1][1] == "https://api.example.com/next"


def test_redirect_limit_is_bounded():
    api, _ = connector(*[RestResponse(302, {"Location": "/next"}, b"", "https://api.example.com/v1") for _ in range(4)])
    with pytest.raises(RestConnectorError, match="redirect limit"):
        api.request(RestRequest("GET", "https://api.example.com/v1", {}), resolver=public_resolver)


def test_oversized_request_and_response_are_blocked():
    api, _ = connector(response(body=b"x" * (256 * 1024 + 1)))
    with pytest.raises(RestConnectorError, match="response"):
        api.request(RestRequest("GET", "https://api.example.com/v1", {}), resolver=public_resolver)

    with pytest.raises(RestConnectorError, match="request body"):
        api.request(RestRequest("POST", "https://api.example.com/v1", {}, b"x" * (64 * 1024 + 1)), approved=True, resolver=public_resolver)


def test_blocked_credential_headers():
    with pytest.raises(RestConnectorError, match="credential-bearing"):
        validate_headers({"Authorization": "Bearer secret"})
    with pytest.raises(RestConnectorError, match="credential-bearing"):
        validate_headers({"Cookie": "session=secret"})


def test_credential_reference_is_metadata_only():
    api, transport = connector(response())
    request = RestRequest("GET", "https://api.example.com/v1", {}, credential_ref="provider:api-key")
    api.request(request, resolver=public_resolver)
    assert "Authorization" not in transport.calls[0][2]
    assert transport.calls[0][2] == {}


def test_write_header_budget_includes_idempotency_key():
    api, _ = connector(response())
    headers = {f"X-Test-{index}": "v" for index in range(39)}
    request = RestRequest("POST", "https://api.example.com/v1", headers, b"x")
    with pytest.raises(RestConnectorError, match="headers"):
        api.request(request, approved=True, resolver=public_resolver)


def test_response_header_budget_is_bounded():
    oversized = {f"X-Test-{index}": "v" for index in range(41)}
    api, _ = connector(RestResponse(200, oversized, b"ok", "https://api.example.com/v1"))
    with pytest.raises(RestConnectorError, match="response headers"):
        api.request(RestRequest("GET", "https://api.example.com/v1", {}), resolver=public_resolver)
