from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from autonomous_agent.action_queue import build_action_proposal, enqueue_proposal, load_queue
from autonomous_agent.server import RuntimeHandler


def _start_server(tmp_path: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = False
    server.server_token = ""
    server.approval_queue_path = tmp_path / "approval_queue.json"
    server.approval_dir = tmp_path / "approvals"
    server.approval_audit_path = tmp_path / "approval_audit.jsonl"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _close(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def _seed_action(tmp_path: Path):
    queue_path = tmp_path / "approval_queue.json"
    proposal = build_action_proposal(
        "update the project configuration",
        ("change the bounded configuration and validate it",),
        llm_requires_approval=True,
    )
    action = enqueue_proposal(queue_path, proposal, risk="high")
    assert action is not None
    return queue_path, action


def test_approval_inbox_lists_only_safe_metadata(tmp_path: Path) -> None:
    queue_path, action = _seed_action(tmp_path)
    server, thread = _start_server(tmp_path)
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/approvals",
            timeout=3,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["approvals"][0]["id"] == action.id
        assert payload["approvals"][0]["risk"] == "high"
        assert "approval_token" not in json.dumps(payload)
        assert "secret" not in json.dumps(payload).lower()
    finally:
        _close(server, thread)


def test_approve_endpoint_uses_existing_approval_store_and_hides_token(tmp_path: Path) -> None:
    queue_path, action = _seed_action(tmp_path)
    server, thread = _start_server(tmp_path)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/approvals/{action.id}/approve",
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["approval"]["action_id"] == action.id
        assert payload["approval"]["status"] == "approved"
        assert "approval_token" not in json.dumps(payload)
        stored = {item.id: item for item in load_queue(queue_path)}
        assert stored[action.id].status == "approved"
    finally:
        _close(server, thread)


def test_reject_endpoint_records_existing_queue_decision(tmp_path: Path) -> None:
    queue_path, action = _seed_action(tmp_path)
    server, thread = _start_server(tmp_path)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/approvals/{action.id}/reject",
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["approval"]["action_id"] == action.id
        assert payload["approval"]["status"] == "rejected"
        stored = {item.id: item for item in load_queue(queue_path)}
        assert stored[action.id].status == "rejected"
    finally:
        _close(server, thread)


def test_mission_control_ui_contains_approval_inbox(tmp_path: Path) -> None:
    server, thread = _start_server(tmp_path)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Approval Inbox" in body
        assert "/api/approvals" in body
    finally:
        _close(server, thread)


def test_approval_inbox_activates_mission_after_operator_approval(tmp_path: Path) -> None:
    server, thread = _start_server(tmp_path)
    try:
        controller = server.mission_controller = __import__("autonomous_agent.mission_control", fromlist=["MissionController"]).MissionController(root=tmp_path)
        controller.approval_queue_path = server.approval_queue_path
        controller.approval_dir = server.approval_dir
        controller.approval_audit_path = server.approval_audit_path
        mission = controller.submit("use the computer to complete this task")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/approvals/{mission.approval_action_id}/approve",
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["approval"]["status"] == "approved"
        assert payload["approval"]["mission"]["state"] == "pending"
        assert payload["approval"]["mission"]["approval_action_id"] == mission.approval_action_id
    finally:
        _close(server, thread)
