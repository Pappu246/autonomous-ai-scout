from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from autonomous_agent.execution_engine import ExecutionState
from autonomous_agent.mission_control import MissionController
from autonomous_agent.server import RuntimeHandler


def _start_server(tmp_path: Path, *, require_auth: bool = False, token: str = ""):
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = require_auth
    server.server_token = token
    server.mission_controller = MissionController(root=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _close(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_mission_ui_is_served(tmp_path: Path):
    server, thread = _start_server(tmp_path)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Mission Control" in body
        assert "Start Mission" in body
    finally:
        _close(server, thread)


def test_create_and_list_mission_over_http(tmp_path: Path):
    server, thread = _start_server(tmp_path)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/missions",
            data=json.dumps({"task": "inspect repository"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            created = json.loads(response.read().decode("utf-8"))
        assert response.status == 202
        mission_id = created["mission"]["mission_id"]
        assert created["mission"]["state"] == "pending"

        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/missions",
            timeout=3,
        ) as response:
            listed = json.loads(response.read().decode("utf-8"))
        assert listed["missions"][0]["mission_id"] == mission_id
    finally:
        _close(server, thread)


def test_cancel_mission_over_http(tmp_path: Path):
    server, thread = _start_server(tmp_path)
    try:
        created_request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/missions",
            data=b'{"task":"inspect repository"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(created_request, timeout=3) as response:
            mission_id = json.loads(response.read().decode("utf-8"))["mission"]["mission_id"]

        cancel = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/missions/{mission_id}/cancel",
            method="POST",
        )
        with urllib.request.urlopen(cancel, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["mission"]["state"] == "cancelled"
    finally:
        _close(server, thread)


def test_mission_api_requires_server_token_when_auth_enabled(tmp_path: Path):
    server, thread = _start_server(tmp_path, require_auth=True, token="expected")
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/missions",
            data=b'{"task":"inspect repository"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=3)
        assert exc_info.value.code == 401

        allowed = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/missions",
            headers={"X-Autonomous-Scout-Token": "expected"},
        )
        with urllib.request.urlopen(allowed, timeout=3) as response:
            assert response.status == 200
    finally:
        _close(server, thread)


def test_blocked_mission_is_reported_without_queueing(tmp_path: Path):
    server, thread = _start_server(tmp_path)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/missions",
            data=b'{"task":"fix the failing tests"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=3)
        assert exc_info.value.code == 422

        mission_id = getattr(server.mission_controller.store.list()[0], "mission_id")
        blocked = server.mission_controller.store.get(mission_id)
        assert blocked is not None
        assert blocked.state == "blocked"
        assert "approval" in blocked.reason.lower()
        assert server.mission_controller.queue.list() == ()
    finally:
        _close(server, thread)
