from __future__ import annotations

import json
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.server import RuntimeHandler, serve
from http.server import ThreadingHTTPServer


def _start_server(*, require_auth: bool = False, token: str = ""):
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = require_auth
    server.server_token = token
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_health_endpoint_reports_runtime_status():
    server, thread = _start_server()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload == {"service": "autonomous-ai-scout", "status": "ok"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_run_endpoint_requires_token_when_auth_is_enabled(monkeypatch):
    called = []

    def forbidden_run_task(*args, **kwargs):
        called.append(True)
        return SimpleNamespace(
            state=ExecutionState.VERIFIED,
            reason="should not execute",
            attempts=1,
            results=(),
            audit_path="state/runtime_execution.jsonl",
        )

    monkeypatch.setattr("autonomous_agent.server.run_task", forbidden_run_task)
    server, thread = _start_server(require_auth=True, token="expected-token")
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/run?task=inspect")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=3)
        assert exc_info.value.code == 401
        assert called == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_run_endpoint_accepts_matching_token(monkeypatch):
    seen = []

    def fake_run_task(task, **kwargs):
        seen.append(task)
        return SimpleNamespace(
            state=ExecutionState.VERIFIED,
            reason="ok",
            attempts=1,
            results=(),
            audit_path="state/runtime_execution.jsonl",
        )

    monkeypatch.setattr("autonomous_agent.server.run_task", fake_run_task)
    server, thread = _start_server(require_auth=True, token="expected-token")
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/run?task=inspect",
            headers={"X-Autonomous-Scout-Token": "expected-token"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["state"] == "verified"
        assert seen == ["inspect"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_public_bind_requires_server_token(monkeypatch):
    monkeypatch.delenv("SCOUT_SERVER_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="SCOUT_SERVER_TOKEN is required"):
        serve("0.0.0.0", 0)


def test_nonloopback_bind_does_not_require_token_when_loopback_host_is_used(monkeypatch):
    monkeypatch.delenv("SCOUT_SERVER_TOKEN", raising=False)
    from autonomous_agent.server import _validate_bind_security
    assert _validate_bind_security("127.0.0.1", "") is False
    assert _validate_bind_security("::1", "") is False
    assert _validate_bind_security("localhost", "") is False


def test_run_endpoint_rejects_oversized_task(monkeypatch):
    called = []

    def fake_run_task(*args, **kwargs):
        called.append(True)
        return SimpleNamespace(
            state=ExecutionState.VERIFIED,
            reason="ok",
            attempts=1,
            results=(),
            audit_path="state/runtime_execution.jsonl",
        )

    monkeypatch.setattr("autonomous_agent.server.run_task", fake_run_task)
    server, thread = _start_server()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/run?task={'x' * 4001}"
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=3)
        assert exc_info.value.code == 400
        assert called == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
