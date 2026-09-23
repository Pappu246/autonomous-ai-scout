from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from autonomous_agent import server as server_module
from autonomous_agent.execution_engine import ExecutionState, ExecutionResult
from autonomous_agent.server import RuntimeHandler, RuntimeHTTPServer, RuntimeTaskManager


def _start(tmp_path):
    manager = RuntimeTaskManager(tmp_path)
    server = RuntimeHTTPServer(("127.0.0.1", 0), RuntimeHandler, manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_health_endpoint_reports_runtime_status(tmp_path):
    server, thread = _start(tmp_path)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["service"] == "autonomous-ai-scout"
        assert payload["status"] == "ok"
        assert payload["root_label"] == tmp_path.name
    finally:
        _stop(server, thread)


def test_web_console_assets_are_served(tmp_path):
    server, thread = _start(tmp_path)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
            html = response.read().decode("utf-8")
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/assets/app.js", timeout=3) as response:
            app_js = response.read().decode("utf-8")
        assert "AUTONOMOUS AI" in html
        assert "Run Task" in html
        assert "/api/tasks" in app_js
    finally:
        _stop(server, thread)


def test_task_submission_endpoint_starts_bounded_background_task(tmp_path, monkeypatch):
    def fake_run_task(task, **kwargs):
        time.sleep(0.05)
        return ExecutionResult(
            ExecutionState.VERIFIED,
            f"verified:{task}",
            1,
            (),
            str(kwargs["audit_path"]),
        )

    monkeypatch.setattr(server_module, "run_task", fake_run_task)
    server, thread = _start(tmp_path)
    try:
        body = json.dumps({"task": "inspect repository"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/tasks",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 202
        task_id = payload["task_id"]

        deadline = time.time() + 3
        final = None
        while time.time() < deadline:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/tasks/{task_id}", timeout=3) as response:
                final = json.loads(response.read().decode("utf-8"))
            if final["state"] == "verified":
                break
            time.sleep(0.03)
        assert final is not None
        assert final["state"] == "verified"
        assert final["reason"] == "verified:inspect repository"
    finally:
        _stop(server, thread)


def test_approval_retry_requeues_blocked_task_with_explicit_approval(tmp_path, monkeypatch):
    calls = []

    def fake_run_task(task, **kwargs):
        calls.append(bool(kwargs.get("explicitly_approved")))
        if not kwargs.get("explicitly_approved"):
            return ExecutionResult(
                ExecutionState.BLOCKED,
                "Authorization blocked for filesystem.transform: tool requires explicit approval",
                0,
                (),
                str(kwargs["audit_path"]),
            )
        return ExecutionResult(
            ExecutionState.VERIFIED,
            "all planned actions executed and verified",
            1,
            (),
            str(kwargs["audit_path"]),
        )

    monkeypatch.setattr(server_module, "run_task", fake_run_task)
    server, thread = _start(tmp_path)
    try:
        body = json.dumps({"task": "transform file config.py"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/tasks",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        task_id = payload["task_id"]

        deadline = time.time() + 3
        blocked = None
        while time.time() < deadline:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/tasks/{task_id}", timeout=3) as response:
                blocked = json.loads(response.read().decode("utf-8"))
            if blocked["state"] == "blocked":
                break
            time.sleep(0.03)
        assert blocked is not None
        assert blocked["approval_required"] is True
        assert calls == [False]

        approve = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/tasks/{task_id}/approve",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(approve, timeout=3) as response:
            approved_payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 202
        assert approved_payload["explicitly_approved"] is True

        approved_id = approved_payload["task_id"]
        deadline = time.time() + 3
        final = None
        while time.time() < deadline:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/tasks/{approved_id}", timeout=3) as response:
                final = json.loads(response.read().decode("utf-8"))
            if final["state"] == "verified":
                break
            time.sleep(0.03)
        assert final is not None
        assert final["state"] == "verified"
        assert final["explicitly_approved"] is True
        assert calls == [False, True]
    finally:
        _stop(server, thread)


def test_runtime_manager_restores_completed_tasks_from_journal(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "runtime_runs.jsonl").write_text(
        json.dumps({
            "execution_id": "restored-1",
            "task": "inspect repository",
            "state": "verified",
            "reason": "all planned actions executed and verified",
            "attempts": 1,
            "result_count": 1,
            "recorded_at": "2026-09-24T00:00:00+00:00",
        }) + "\n",
        encoding="utf-8",
    )

    manager = RuntimeTaskManager(tmp_path)
    tasks = manager.list()

    assert tasks
    assert tasks[0]["execution_id"] == "restored-1"
    assert tasks[0]["state"] == "verified"
    assert tasks[0]["task"] == "inspect repository"


def test_history_and_approval_endpoints_are_bounded(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "runtime_runs.jsonl").write_text(
        json.dumps({
            "execution_id": "e1",
            "task": "inspect repository",
            "state": "verified",
            "reason": "ok",
            "attempts": 1,
            "result_count": 1,
            "recorded_at": "2026-01-01T00:00:00+00:00",
        }) + "\n",
        encoding="utf-8",
    )
    (state / "approval_queue.json").write_text("[]", encoding="utf-8")
    server, thread = _start(tmp_path)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/history?limit=10", timeout=3) as response:
            history = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/approvals", timeout=3) as response:
            approvals = json.loads(response.read().decode("utf-8"))
        assert history["records"][0]["execution_id"] == "e1"
        assert approvals["pending_count"] == 0
    finally:
        _stop(server, thread)


def test_legacy_run_endpoint_is_preserved(tmp_path, monkeypatch):
    def fake_run_task(task, **kwargs):
        return ExecutionResult(ExecutionState.VERIFIED, "ok", 1, (), str(kwargs["audit_path"]))

    monkeypatch.setattr(server_module, "run_task", fake_run_task)
    server, thread = _start(tmp_path)
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/run?task=inspect%20repository",
            timeout=3,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["state"] == "verified"
    finally:
        _stop(server, thread)


def test_runtime_manager_rejects_task_over_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "run_task", lambda *args, **kwargs: time.sleep(0.3))
    manager = RuntimeTaskManager(tmp_path, max_active=1)
    first = manager.submit("first task")
    try:
        try:
            manager.submit("second task")
        except RuntimeError as exc:
            assert "capacity" in str(exc)
        else:
            raise AssertionError("capacity limit must reject additional tasks")
    finally:
        time.sleep(0.35)
