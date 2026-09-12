from __future__ import annotations

import pytest
from dataclasses import dataclass

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


def connector(*responses):
    transport = FakeTransport(list(responses), [])
    return RestConnector({"api.example.com"}, transport=transport), transport


def test_url_requires_https_and_allowlist():
    with pytest.raises(RestConnectorError):
        validate_public_https_url("http://api.example.com/v1", frozenset({"api.example.com"}))
    with pytest.raises(RestConnectorError):
        validate_public_https_url("https://other.example.com/v1", frozenset({"api.example.com"}))
    with pytest.raises(RestConnectorError):
        validate_public_https_url("https://user:pass@api.example.com/v1", frozenset({"api.example.com"}))


def test_url_blocks_non_public_literal_addresses():
    allowed = frozenset({"127.0.0.1", "10.0.0.1", "169.254.169.254"})
    for url in (
        "https://127.0.0.1/",
        "https://10.0.0.1/",
        "https://169.254.169.254/",
    ):
        with pytest.raises(RestConnectorError):
            validate_public_https_url(url, allowed)


def test_read_succeeds_and_redacts_secrets():
    api, transport = connector(response(body=b'{"api_key=secret-value":true}'))
    result = api.safe_json(RestRequest("GET", "https://api.example.com/v1", {"Accept": "application/json"}))
    assert result["status_code"] == 200
    assert "[REDACTED]" in result["body"]
    assert transport.calls[0][0] == "GET"


def test_write_requires_human_approval_and_adds_idempotency():
    api, transport = connector(response())
    request = RestRequest("POST", "https://api.example.com/v1", {"Content-Type": "application/json"}, b'{"x":1}')
    with pytest.raises(RestConnectorError, match="human approval"):
        api.request(request)
    result = api.request(request, approved=True)
    expected = deterministic_idempotency_key("POST", request.url, request.body)
    assert result.status_code == 200
    assert transport.calls[-1][2]["Idempotency-Key"] == expected


def test_safe_methods_retry_but_writes_do_not():
    api, transport = connector(response(503), response(200))
    assert api.request(RestRequest("GET", "https://api.example.com/v1", {})).status_code == 200
    assert len(transport.calls) == 2

    api, transport = connector(response(503), response(200))
    assert api.request(RestRequest("POST", "https://api.example.com/v1", {}, b"x"), approved=True).status_code == 503
    assert len(transport.calls) == 1


def test_redirect_must_remain_allowlisted_https():
    api, _ = connector(RestResponse(302, {"Location": "https://evil.example.com/x"}, b"", "https://api.example.com/v1"))
    with pytest.raises(RestConnectorError, match="allowlisted"):
        api.request(RestRequest("GET", "https://api.example.com/v1", {}))


def test_oversized_request_and_response_are_blocked():
    api, _ = connector(response(body=b"x" * (256 * 1024 + 1)))
    with pytest.raises(RestConnectorError, match="response"):
        api.request(RestRequest("GET", "https://api.example.com/v1", {}))

    with pytest.raises(RestConnectorError, match="request body"):
        api.request(RestRequest("POST", "https://api.example.com/v1", {}, b"x" * (64 * 1024 + 1)), approved=True)


def test_blocked_credential_headers():
    with pytest.raises(RestConnectorError, match="credential-bearing"):
        validate_headers({"Authorization": "Bearer secret"})
