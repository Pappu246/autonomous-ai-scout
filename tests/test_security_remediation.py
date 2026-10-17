"""Deterministic regressions for security and mission-history failures.

All credentials here are synthetic and all execution is confined to pytest's
isolated temporary workspace; no cloud API, real approval or external action.
"""
from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from autonomous_agent.action_queue import PendingAction, load_queue
from autonomous_agent.approval import pending_actions, set_decision
from autonomous_agent.approval_store import create_approval
from autonomous_agent.mission_control import MissionController, MissionRecord
from autonomous_agent.server import RuntimeHandler, _mission_ui, _validate_bind_security
from autonomous_agent.task_queue import QueueState
from autonomous_agent.vercel_proxy import ScoutVercelProxyHandler, target_is_allowed


def test_non_loopback_runtime_rejects_weak_backend_token() -> None:
    with pytest.raises(RuntimeError, match="32"):
        _validate_bind_security("0.0.0.0", "short")
    assert _validate_bind_security("0.0.0.0", "safe" * 9)
    assert not _validate_bind_security("127.0.0.1", "")


def test_mission_metadata_never_persists_embedded_credential_text(tmp_path: Path) -> None:
    # A fixture assembled at runtime avoids storing a key-shaped literal in Git.
    synthetic_value = "example" * 7
    controller = MissionController(root=tmp_path)
    task = "inspect repository " + "token=" + synthetic_value
    mission = controller.submit(task)

    assert synthetic_value not in mission.task
    assert synthetic_value not in (tmp_path / "state" / "missions.json").read_text()
    assert synthetic_value not in (tmp_path / "state" / "mission_queue.json").read_text()

    plan = controller.submit_plan(
        "inspect repository " + "password=" + synthetic_value,
        ({"node_id": "read", "task": "inspect repository " + "token=" + synthetic_value,
          "depends_on": ()},),
    )
    assert synthetic_value not in plan.task
    assert all(synthetic_value not in str(step) for step in plan.steps)
    assert synthetic_value not in (tmp_path / "state" / "missions.json").read_text()


def test_fallback_session_is_opaque_and_expires_on_server(monkeypatch) -> None:
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    synthetic_credential = "safe" * 9
    monkeypatch.setenv("SCOUT_WEB_ACCESS_TOKEN", synthetic_credential)
    handler = object.__new__(ScoutVercelProxyHandler)
    handler.path = "/login"
    handler.command = "POST"
    body = json.dumps({"token": synthetic_credential}).encode()
    handler.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
    handler.rfile = io.BytesIO(body)
    replies = []
    monkeypatch.setattr(handler, "_json", lambda status, payload, **options: replies.append((status, payload, options)))

    handler.do_POST()
    assert replies[0][0] == 200
    set_cookie = replies[0][2]["extra_headers"]["Set-Cookie"]
    cookies = SimpleCookie()
    cookies.load(set_cookie)
    session = cookies["scout_session"].value
    assert session != synthetic_credential
    assert synthetic_credential not in set_cookie
    assert "Secure" in set_cookie and "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie

    handler.headers = {"Cookie": f"scout_session={session}"}
    assert handler._has_session()
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 43_201)
    assert not handler._has_session(), "browser cookie expiry alone cannot prevent replay"


def test_old_raw_workspace_token_is_not_accepted_as_a_cookie(monkeypatch) -> None:
    monkeypatch.setenv("SCOUT_WEB_ACCESS_TOKEN", "safe" * 9)
    handler = object.__new__(ScoutVercelProxyHandler)
    handler.headers = {"Cookie": "scout_session=" + "safe" * 9}
    assert not handler._has_session()


@pytest.mark.parametrize("path, method", [
    ("/api/missions/%2e%2e%2f%2e%2e%2frun", "GET"),
    ("/api/missions/%2Frun/timeline", "GET"),
    ("/api/approvals/%2e%2e/approve", "POST"),
    ("/api/missions/mis%73ion-123/resume", "POST"),
])
def test_gateway_rejects_encoded_or_noncanonical_resource_ids(path: str, method: str) -> None:
    assert not target_is_allowed(path, method)


def test_gateway_never_forwards_a_cacheable_backend_policy(monkeypatch) -> None:
    handler = object.__new__(ScoutVercelProxyHandler)
    handler.command = "GET"
    handler.wfile = io.BytesIO()
    headers = []
    monkeypatch.setattr(handler, "send_response", lambda status: None)
    monkeypatch.setattr(handler, "send_header", lambda key, value: headers.append((key.lower(), value)))
    monkeypatch.setattr(handler, "end_headers", lambda: None)

    handler._reply(200, b"{}", extra_headers={"Cache-Control": "public, max-age=3600"})

    assert [value for key, value in headers if key == "cache-control"] == ["no-store"]


def test_blocked_or_expired_approval_cannot_be_reactivated(tmp_path: Path) -> None:
    path = tmp_path / "approval_queue.json"
    old = (datetime.now(UTC) - timedelta(days=9)).isoformat()
    actions = [
        PendingAction("blocked-action", "change project", ("inspect",), "high", "needs review", "blocked", old),
        PendingAction("expired-action", "change project", ("inspect",), "high", "needs review", "pending", old),
    ]
    path.write_text(json.dumps([asdict(action) for action in actions]), encoding="utf-8")
    assert pending_actions(path) == []
    for action in actions:
        with pytest.raises(ValueError):
            set_decision(path, action.id, "approved")
    stored = json.loads(path.read_text())
    assert [row["status"] for row in stored] == ["blocked", "pending"]


def test_undated_or_future_approval_is_not_actionable(tmp_path: Path) -> None:
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    queue = tmp_path / "approval_queue.json"
    records = [
        PendingAction("undated", "inspect project", ("inspect",), "high", "review", "pending", ""),
        PendingAction("future", "inspect project", ("inspect",), "high", "review", "pending", future),
    ]
    queue.write_text(json.dumps([asdict(r) for r in records]), encoding="utf-8")
    assert pending_actions(queue) == []
    for record in records:
        with pytest.raises(ValueError):
            set_decision(queue, record.id, "approved")


def test_identical_side_effect_missions_need_distinct_approvals(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    first = controller.submit("use the computer to complete this task")
    second = controller.submit("use the computer to complete this task")

    assert first.mission_id != second.mission_id
    assert first.approval_action_id and second.approval_action_id
    assert first.approval_action_id != second.approval_action_id
    pending = {a.id for a in load_queue(controller.approval_queue_path) if a.status == "pending"}
    assert pending == {first.approval_action_id, second.approval_action_id}

    create_approval(controller.approval_queue_path, controller.approval_dir,
                    first.approval_action_id, audit_path=controller.approval_audit_path)
    assert controller.activate_approved_action(first.approval_action_id).state == "pending"
    assert controller.store.get(second.mission_id).state == "requires_approval"
    assert len(controller.queue.list()) == 1


def test_approved_activation_writes_mission_before_queue_is_claimable(tmp_path: Path, monkeypatch) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("use the computer to complete this task")
    create_approval(controller.approval_queue_path, controller.approval_dir,
                    mission.approval_action_id, audit_path=controller.approval_audit_path)
    enqueue = controller.queue.enqueue

    def observe_before_enqueue(task, *, task_id, execution_id, **kwargs):
        current = controller.store.get(mission.mission_id)
        assert current is not None
        assert (current.task_id, current.execution_id, current.state) == (task_id, execution_id, "pending")
        assert current.approval_action_id == mission.approval_action_id
        return enqueue(task, task_id=task_id, execution_id=execution_id, **kwargs)

    monkeypatch.setattr(controller.queue, "enqueue", observe_before_enqueue)
    activated = controller.activate_approved_action(mission.approval_action_id)
    assert activated is not None and activated.state == "pending"


def test_resume_writes_mission_before_queue_is_claimable(tmp_path: Path, monkeypatch) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")
    controller.store.put(MissionRecord(**{**mission.__dict__, "state": "failed"}))
    enqueue = controller.queue.enqueue

    def observe_before_enqueue(task, *, task_id, execution_id, **kwargs):
        current = controller.store.get(mission.mission_id)
        assert current is not None
        assert (current.task_id, current.execution_id, current.state) == (task_id, execution_id, "pending")
        return enqueue(task, task_id=task_id, execution_id=execution_id, **kwargs)

    monkeypatch.setattr(controller.queue, "enqueue", observe_before_enqueue)
    resumed = controller.resume(mission.mission_id)
    assert resumed.state == "pending"
    assert resumed.task_id != mission.task_id


def test_worker_refuses_queue_item_without_mission_metadata(tmp_path: Path, monkeypatch) -> None:
    controller = MissionController(root=tmp_path)
    controller.queue.enqueue("inspect repository", task_id="orphan", execution_id="exec-orphan")
    executed = []

    def unexpected_execution(*args, **kwargs):
        executed.append(True)
        raise AssertionError("orphan task must not run")

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", unexpected_execution)
    controller.run_once()
    assert executed == []
    assert controller.queue.list()[0].state is QueueState.FAILED


@pytest.mark.parametrize("operation", ["activate", "resume"])
def test_queue_failure_leaves_visible_failed_mission(tmp_path: Path, monkeypatch, operation: str) -> None:
    controller = MissionController(root=tmp_path)
    if operation == "activate":
        mission = controller.submit("use the computer to complete this task")
        create_approval(controller.approval_queue_path, controller.approval_dir,
                        mission.approval_action_id, audit_path=controller.approval_audit_path)
        callback = lambda: controller.activate_approved_action(mission.approval_action_id)
    else:
        mission = controller.submit("inspect repository")
        controller.store.put(MissionRecord(**{**mission.__dict__, "state": "failed"}))
        callback = lambda: controller.resume(mission.mission_id)

    def unavailable_queue(*args, **kwargs):
        raise TimeoutError("simulated queue persistence outage")

    monkeypatch.setattr(controller.queue, "enqueue", unavailable_queue)
    with pytest.raises(TimeoutError):
        callback()
    current = controller.store.get(mission.mission_id)
    assert current is not None and current.state == "failed"
    assert current.reason == "mission queue persistence failed"


def test_high_level_architecture_request_is_not_verified_by_a_file_listing(tmp_path: Path) -> None:
    from autonomous_agent.specialist_provider import SpecialistProviderRouter

    (tmp_path / "README.md").write_text("# Isolated architecture fixture\n", encoding="utf-8")
    controller = MissionController(root=tmp_path)
    controller.provider_router = SpecialistProviderRouter(())
    mission = controller.submit("inspect the repository and summarize its architecture")
    result = controller.run_once()
    assert result is not None and result.mission_id == mission.mission_id
    assert result.state == "partial"
    assert "NOT an AI-generated review" in result.reason or "no model-written" in result.reason
    assert controller.queue.list()[0].state is QueueState.FAILED


def test_requested_file_summary_runs_read_tool_but_does_not_claim_a_written_summary(tmp_path: Path) -> None:
    from autonomous_agent.specialist_provider import SpecialistProviderRouter

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("# Harmless fixture\nTwo benign facts.\n")
    controller = MissionController(root=tmp_path)
    controller.provider_router = SpecialistProviderRouter(())
    mission = controller.submit(
        "Read file docs/README.md in the isolated workspace and summarize two facts about the project"
    )
    result = controller.run_once()

    assert result is not None and result.state == "partial"
    assert any(event.get("event") == "tool_result" and event.get("tool") == "filesystem.read"
               for event in controller.timeline(mission.mission_id))
    assert controller.queue.list()[0].state is QueueState.FAILED


def test_selecting_a_provider_is_not_evidence_of_generative_execution(tmp_path: Path) -> None:
    from autonomous_agent.specialist_provider import ProviderRoleRoute

    (tmp_path / "README.md").write_text("# Isolated fixture\n", encoding="utf-8")
    controller = MissionController(root=tmp_path)

    class SelectionOnly:
        def route(self, role):
            provider = type("Candidate", (), {"name": "synthetic-selection-only"})()
            return ProviderRoleRoute(str(role), provider, True, "selection only; no model call")

    controller.provider_router = SelectionOnly()
    mission = controller.submit("review the repository and explain security risks")
    result = controller.run_once()
    assert result is not None and result.mission_id == mission.mission_id
    assert result.state == "partial"
    assert "no model-written" in result.reason or "not an AI-generated review" in result.reason.lower()
    timeline = controller.timeline(mission.mission_id)
    assert any(event.get("event") == "specialist_provider_route" and event.get("selection_only") == "True"
               for event in timeline)


def test_partial_mission_is_terminal_and_can_be_resumed_after_provider_setup(tmp_path: Path) -> None:
    from autonomous_agent.specialist_provider import SpecialistProviderRouter

    controller = MissionController(root=tmp_path)
    controller.provider_router = SpecialistProviderRouter(())
    mission = controller.submit("inspect the project status and summarize its structure, recent status and visible issues")
    result = controller.run_once()
    assert result is not None and result.state == "partial"
    assert controller.queue.list()[0].state is QueueState.FAILED
    assert result.terminal
    resumed = controller.resume(mission.mission_id)
    assert resumed.state == "pending" and resumed.task_id != mission.task_id
    assert "'partial'" in _mission_ui()


def test_verified_tool_cannot_leave_mission_running_when_memory_write_fails(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.execution_engine import ExecutionState

    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")
    monkeypatch.setattr(
        "autonomous_agent.mission_control.run_task",
        lambda *args, **kwargs: type("Result", (), {"state": ExecutionState.VERIFIED,
                                                 "reason": "verified by isolated fixture", "results": ()})(),
    )

    def unavailable(*args, **kwargs):
        raise TimeoutError("synthetic memory storage outage")

    monkeypatch.setattr(controller.memory, "record_episode", unavailable)
    result = controller.run_once()
    assert result is not None and result.mission_id == mission.mission_id
    assert result.state == "partial"
    assert "memory" in result.reason.lower() and "unavailable" in result.reason.lower()
    assert controller.queue.list()[0].state is QueueState.FAILED
    assert any(item.get("state") == "partial" for item in controller.timeline(mission.mission_id))


def test_memory_silent_persistence_failure_does_not_claim_verified(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.execution_engine import ExecutionState

    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")
    monkeypatch.setattr(
        "autonomous_agent.mission_control.run_task",
        lambda *args, **kwargs: type("Result", (), {"state": ExecutionState.VERIFIED,
                                                 "reason": "verified by isolated fixture", "results": ()})(),
    )
    monkeypatch.setattr(controller.memory.store, "_save", lambda *_args: False)
    result = controller.run_once()
    assert result is not None and result.mission_id == mission.mission_id
    assert result.state == "partial" and "memory" in result.reason.lower()
    assert controller.queue.list()[0].state is QueueState.FAILED


def test_html_escape_is_safe_for_both_text_and_quoted_attributes() -> None:
    page = _mission_ui()
    assert 'node.innerHTML.replaceAll(\'"\', \'&quot;\').replaceAll("\'", \'&#39;\')' in page
    assert 'data-memory="${esc(item.mission_id)}"' in page
    assert 'data-approve="${esc(item.id)}"' in page


def test_runtime_html_has_effective_security_response_headers(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = False
    server.server_token = ""
    server.workspace_root = tmp_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
            assert response.status == 200
            assert response.headers.get("X-Frame-Options") == "DENY"
            assert response.headers.get("X-Content-Type-Options") == "nosniff"
            assert "frame-ancestors 'none'" in response.headers.get("Content-Security-Policy", "")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_corrupt_approval_queue_cannot_look_empty_or_be_overwritten(tmp_path: Path) -> None:
    queue_path = tmp_path / "state" / "approval_queue.json"
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text("broken-approval-queue-fixture", encoding="utf-8")
    controller = MissionController(root=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = False
    server.server_token = ""
    server.workspace_root = tmp_path
    server.mission_controller = controller
    server.approval_queue_path = queue_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/approvals", timeout=3)
        assert error.value.code == 503
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/missions",
                data=b'{"task":"use the computer to complete this task"}',
                headers={"Content-Type": "application/json"}, method="POST",
            ), timeout=3)
        assert error.value.code == 503
        assert queue_path.read_text(encoding="utf-8") == "broken-approval-queue-fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_corrupt_memory_is_reported_unavailable_not_as_empty_history(tmp_path: Path) -> None:
    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")
    controller.memory.store.path.write_text("invalid-memory-fixture", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = False
    server.server_token = ""
    server.workspace_root = tmp_path
    server.mission_controller = controller
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/missions/{mission.mission_id}/memory"
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url, timeout=3)
        assert error.value.code == 503
        assert "temporarily unavailable" in error.value.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_runtime_reports_shared_storage_failure_as_safe_http_error(tmp_path: Path, monkeypatch) -> None:
    controller = MissionController(root=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = False
    server.server_token = ""
    server.workspace_root = tmp_path
    server.mission_controller = controller
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def unavailable(*args, **kwargs):
        raise TimeoutError("simulated storage outage with sensitive diagnostic")

    try:
        monkeypatch.setattr(controller.store, "list", unavailable)
        for request in (
            urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/missions"),
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/missions",
                data=b'{"task":"inspect repository"}',
                headers={"Content-Type": "application/json"}, method="POST",
            ),
        ):
            if request.get_method() == "POST":
                monkeypatch.setattr(controller, "submit", unavailable)
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=3)
            assert error.value.code == 503
            message = error.value.read().decode()
            assert "temporarily unavailable" in message
            assert "sensitive diagnostic" not in message
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
