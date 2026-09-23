from __future__ import annotations

import httpx

import pytest

from autonomous_agent import sources


class FakeResponse:
    def __init__(self, status_code=200, content=b"<title>Example</title>public content", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError("http error")


class FakeClient:
    responses = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        return self.responses.pop(0)


def test_validate_source_url_rejects_insecure_local_and_private_targets():
    for url in (
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com:8443",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://172.16.0.1",
        "https://192.168.1.1",
        "https://169.254.169.254",
    ):
        with pytest.raises(ValueError):
            sources._validate_source_url(url)


def test_fetch_source_accepts_bounded_same_host_https_redirect(monkeypatch):
    FakeClient.responses = [
        FakeResponse(302, b"", {"location": "https://example.com/final"}),
        FakeResponse(200, b"<title>Example</title><p>ok</p>"),
    ]
    monkeypatch.setattr(sources.httpx, "Client", FakeClient)
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))])

    result = sources.fetch_source("https://example.com/start")

    assert result.reachable
    assert result.url == "https://example.com/start"
    assert result.title == "Example"
    assert "ok" in result.text


def test_fetch_source_rejects_cross_host_redirect(monkeypatch):
    FakeClient.responses = [
        FakeResponse(302, b"", {"location": "https://attacker.example/"}),
    ]
    monkeypatch.setattr(sources, "httpx", SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))])

    result = sources.fetch_source("https://example.com/start")

    assert not result.reachable


def test_fetch_source_rejects_oversized_response(monkeypatch):
    FakeClient.responses = [
        FakeResponse(200, b"x" * (sources._MAX_SOURCE_BYTES + 1)),
    ]
    monkeypatch.setattr(sources, "httpx", SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))])

    result = sources.fetch_source("https://example.com")

    assert not result.reachable


def test_fetch_source_fails_closed_on_http_error(monkeypatch):
    FakeClient.responses = [FakeResponse(503, b"unavailable")]
    monkeypatch.setattr(sources, "httpx", SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))])

    result = sources.fetch_source("https://example.com")

    assert not result.reachable
