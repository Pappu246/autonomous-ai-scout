"""Authenticated local HTTP canary; no real account, network tool or mutation."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from autonomous_agent.mission_control import MissionController
from autonomous_agent.server import RuntimeHandler
from autonomous_agent.specialist_provider import SpecialistProviderRouter
from autonomous_agent.task_queue import QueueState


def test_authenticated_http_mission_executes_read_only_task_with_evidence(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text(
        "# Isolated fixture\nThis is a harmless temporary-workspace document.\n",
        encoding="utf-8",
    )
    controller = MissionController(root=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = True
    server.server_token = "synthetic-local-test-token"
    server.workspace_root = tmp_path
    server.mission_controller = controller
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path: str, *, method: str = "GET", body: dict | None = None,
                authorized: bool = True):
        headers = {"X-Autonomous-Scout-Token": server.server_token} if authorized else {}
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=8) as response:
            contents = response.read()
            if response.headers.get("Content-Type", "").startswith("application/json"):
                return response.status, json.loads(contents)
            return response.status, contents.decode("utf-8")

    try:
        for path, method, body in [
            ("/", "GET", None), ("/api/missions", "GET", None),
            ("/api/approvals", "GET", None), ("/run?task=inspect", "GET", None),
            ("/api/missions", "POST", {"task": "inspect repository"}),
            ("/api/approvals/synthetic/approve", "POST", None),
        ]:
            with pytest.raises(urllib.error.HTTPError) as unauthorized:
                request(path, method=method, body=body, authorized=False)
            assert unauthorized.value.code == 401
        assert controller.store.list() == ()

        status, health = request("/health", authorized=False)
        assert status == 200 and health["status"] == "ok" and "workspace_root" not in health
        status, ui = request("/")
        assert status == 200 and "Mission Control" in ui

        task = (
            "Read file docs/README.md in the isolated workspace. "
            "Do not change any file. Do not execute shell commands. Do not use network, "
            "browser, GitHub, email, calendar, applications, or computer control."
        )
        status, created = request("/api/missions", method="POST", body={"task": task})
        assert status == 202
        mission_id = created["mission"]["mission_id"]
        assert mission_id.startswith("mission-") and created["mission"]["state"] == "pending"
        status, listed = request("/api/missions")
        assert status == 200 and mission_id in {row["mission_id"] for row in listed["missions"]}

        performed = controller.run_once()
        assert performed is not None and performed.mission_id == mission_id
        assert performed.state == "verified", performed.reason
        assert controller.queue.list()[0].state is QueueState.SUCCEEDED
        status, detail = request("/api/missions/" + mission_id)
        assert status == 200 and detail["mission"]["state"] == "verified"
        status, timeline = request("/api/missions/" + mission_id + "/timeline")
        assert status == 200
        assert any(item.get("event") == "tool_result" and item.get("tool") == "filesystem.read"
                   for item in timeline["timeline"])
        assert any(item.get("event") == "mission_state" and item.get("state") == "verified"
                   for item in timeline["timeline"])
        status, memory = request("/api/missions/" + mission_id + "/memory")
        assert status == 200 and len([m for m in memory["memory"] if m["outcome"] == "verified"]) == 1
        assert (tmp_path / "docs" / "README.md").read_text().startswith("# Isolated fixture")

        # Side effects are not made executable merely by queuing an approval.
        status, gated = request("/api/missions", method="POST",
                                body={"task": "use the computer to complete this task"})
        assert status == 202 and gated["mission"]["state"] == "requires_approval"
        assert gated["mission"]["approval_action_id"]
        assert len(controller.queue.list()) == 1
        status, inbox = request("/api/approvals")
        assert status == 200
        assert any(row["id"] == gated["mission"]["approval_action_id"]
                   for row in inbox["approvals"])

        # An unconfigured generative provider yields a factual PARTIAL result,
        # not a fabricated project analysis or a falsely VERIFIED mission.
        controller.provider_router = SpecialistProviderRouter(())
        status, partial = request("/api/missions", method="POST", body={
            "task": "Inspect the project status and summarize its structure, recent status and visible issues"
        })
        assert status == 202 and partial["mission"]["state"] == "pending"
        controller.run_once()
        status, partial_detail = request("/api/missions/" + partial["mission"]["mission_id"])
        assert status == 200 and partial_detail["mission"]["state"] == "partial"
        assert "NOT an AI-generated review" in partial_detail["mission"]["reason"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
