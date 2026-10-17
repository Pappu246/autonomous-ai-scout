"""Reproduced state-machine failures; no fake-Blob test proves global exactly-once."""
from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from autonomous_agent.action_queue import (
    build_action_proposal,
    enqueue_proposal,
    load_queue,
)
from autonomous_agent.approval_store import create_approval, load_approval
from autonomous_agent.background_worker import BackgroundTaskWorker
from autonomous_agent.filesystem_workspace import WorkspaceConnector, WorkspaceError
from autonomous_agent.mission_control import MissionController
from autonomous_agent.task_queue import QueueState, TaskQueueStore


def test_repeating_decided_approval_gets_new_identity_and_fresh_record(tmp_path: Path) -> None:
    queue = tmp_path / "state" / "approval_queue.json"
    directory = tmp_path / "state" / "approvals"
    audit = tmp_path / "state" / "approval_audit.jsonl"
    proposal = build_action_proposal("edit synthetic source", ("inspect the change",), True)
    first = enqueue_proposal(queue, proposal, risk="high")
    assert first is not None
    approved_first = create_approval(queue, directory, first.id, audit_path=audit)
    second = enqueue_proposal(queue, proposal, risk="high")
    assert second is not None and second.id != first.id
    assert len({item.id for item in load_queue(queue)}) == 2
    assert second.status == "pending"
    assert enqueue_proposal(queue, proposal, risk="high").id == second.id
    approved_second = create_approval(queue, directory, second.id, audit_path=audit)
    assert approved_second.approval_token != approved_first.approval_token
    assert load_approval(directory, first.id) == approved_first
    assert load_approval(directory, second.id) == approved_second


def test_old_worker_cannot_complete_a_reclaimed_queue_attempt(tmp_path: Path) -> None:
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1")
    old = queue.claim_next()
    assert old is not None
    queue.recover_running()
    queue.confirm_recovery(old.task_id)
    current = queue.claim_next()
    assert current is not None and current.attempts == old.attempts + 1
    with pytest.raises(ValueError, match="claim|attempt|current"):
        queue.complete(old.task_id, success=True,
                       expected_execution_id=old.execution_id, expected_attempt=old.attempts)
    assert queue.list()[0].state is QueueState.RUNNING
    updated = queue.complete(current.task_id, success=True,
                             expected_execution_id=current.execution_id, expected_attempt=current.attempts)
    assert updated.state is QueueState.SUCCEEDED


def test_worker_does_not_mark_new_attempt_succeeded_after_handler_was_superseded(tmp_path: Path) -> None:
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1")

    def superseded(item):
        queue.recover_running()
        queue.confirm_recovery(item.task_id)
        current = queue.claim_next()
        assert current is not None and current.attempts == 2
        return True

    worker = BackgroundTaskWorker(queue, superseded)
    worker.run_once()
    current = queue.list()[0]
    assert current.state is QueueState.RUNNING and current.attempts == 2


def test_identical_mission_requests_keep_independent_memory_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "README.md"
    facts = "The first source fact is synthetic.\nThe second source fact is local.\n"
    path.write_text(facts, encoding="utf-8")
    controller = MissionController(root=tmp_path)
    task = "Read file README.md and summarize two facts."
    first = controller.submit(task)
    completed = controller.run_once()
    assert completed is not None and completed.state == "verified" and completed.output

    path.write_text("Only one source fact is now available.\n", encoding="utf-8")
    second = controller.submit(task)
    incomplete = controller.run_once()
    assert incomplete is not None and incomplete.state == "partial" and incomplete.output == ""
    assert first.mission_id != second.mission_id
    history = controller.memory.history(controller.MEMORY_PROJECT, kind="episode")
    by_mission = {
        item.get("data", {}).get("metadata", {}).get("mission_id"): item
        for item in history
    }
    assert by_mission[first.mission_id]["outcome"] == "verified"
    assert by_mission[first.mission_id]["data"]["summary"] == completed.output
    assert by_mission[second.mission_id]["outcome"] == "partial"
    assert controller.store.get(first.mission_id).state == "verified"
    assert controller.store.get(second.mission_id).state == "partial"


def test_full_approval_queue_does_not_claim_a_dropped_action_was_persisted(tmp_path: Path) -> None:
    import json
    from dataclasses import asdict
    from datetime import datetime

    from autonomous_agent.action_queue import MAX_PENDING_ACTIONS, PendingAction

    path = tmp_path / "state" / "approval_queue.json"
    path.parent.mkdir(parents=True)
    now = datetime.now(UTC).isoformat()
    existing = [PendingAction(f"high-{index}", f"edit synthetic {index}", ("inspect",),
                              "high", "review", "pending", now)
                for index in range(MAX_PENDING_ACTIONS)]
    path.write_text(json.dumps([asdict(item) for item in existing]), encoding="utf-8")
    proposal = build_action_proposal("edit another synthetic source", ("inspect",), True)
    with pytest.raises(RuntimeError, match="full|capacity"):
        enqueue_proposal(path, proposal, risk="low")
    assert {item.id for item in load_queue(path)} == {item.id for item in existing}


def test_stale_worker_cannot_replace_a_resumed_mission_record(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.execution_engine import ExecutionState

    controller = MissionController(root=tmp_path)
    submitted = controller.submit("inspect repository")
    resumed = {}

    def old_result(_task, **_kwargs):
        recovered = controller.queue.recover_running()
        controller._persist_recovered_items(recovered)
        resumed["record"] = controller.resume(submitted.mission_id)
        return type("Result", (), {"state": ExecutionState.VERIFIED,
                                   "reason": "old attempt completed late", "results": ()})()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", old_result)
    controller.run_once()
    current = controller.store.get(submitted.mission_id)
    assert current is not None and current.task_id == resumed["record"].task_id
    assert current.execution_id == resumed["record"].execution_id
    assert current.state == "pending"
    assert controller.queue.list()[-1].state is QueueState.PENDING


def test_memory_endpoint_is_scoped_to_mission_not_identical_task_text(tmp_path: Path) -> None:
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from autonomous_agent.server import RuntimeHandler

    readme = tmp_path / "README.md"
    readme.write_text("The first fact is synthetic.\nThe second fact is local.\n", encoding="utf-8")
    controller = MissionController(root=tmp_path)
    task = "Read file README.md and summarize two facts."
    first = controller.submit(task)
    assert controller.run_once().state == "verified"
    readme.write_text("Only one fact remains here.\n", encoding="utf-8")
    second = controller.submit(task)
    assert controller.run_once().state == "partial"
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = True
    server.server_token = "synthetic-test-only-token"
    server.workspace_root = tmp_path
    server.mission_controller = controller
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for mission, state in ((first, "verified"), (second, "partial")):
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/missions/{mission.mission_id}/memory",
                headers={"X-Autonomous-Scout-Token": server.server_token},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                rows = json.loads(response.read())["memory"]
            assert len(rows) == 1 and rows[0]["outcome"] == state
            assert rows[0]["data"]["metadata"]["mission_id"] == mission.mission_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_explicitly_reclaimed_attempt_can_continue_without_stale_worker_result(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.execution_engine import ExecutionState

    controller = MissionController(root=tmp_path)
    mission = controller.submit("inspect repository")
    claimed_new = {}
    calls = {"count": 0}

    def run_with_recovery(_task, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            controller.queue.recover_running()
            controller.queue.confirm_recovery(mission.task_id)
            claimed_new["item"] = controller.queue.claim_next()
        return type("Result", (), {"state": ExecutionState.VERIFIED,
                                   "reason": f"attempt {calls['count']} completed", "results": ()})()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", run_with_recovery)
    controller.run_once()
    assert controller.store.get(mission.mission_id).state != "verified"
    second = claimed_new["item"]
    assert second is not None and second.attempts == 2
    assert controller._handle_queue_item(second)
    controller.queue.complete(second.task_id, success=True,
                              expected_execution_id=second.execution_id, expected_attempt=second.attempts)
    assert controller.store.get(mission.mission_id).state == "verified"
    assert "attempt 2" in controller.store.get(mission.mission_id).reason


def test_stale_dag_step_cannot_advance_a_resumed_mission(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.execution_engine import ExecutionState

    controller = MissionController(root=tmp_path)
    submitted = controller.submit_plan(
        "inspect repository", ({"node_id": "inspect", "task": "inspect repository", "depends_on": ()},)
    )
    resumed = {}

    def late_step(_task, **_kwargs):
        controller._persist_recovered_items(controller.queue.recover_running())
        resumed["record"] = controller.resume(submitted.mission_id)
        return type("Result", (), {"state": ExecutionState.VERIFIED,
                                   "reason": "late step result", "results": ()})()

    monkeypatch.setattr("autonomous_agent.mission_control.run_task", late_step)
    controller.run_once()
    current = controller.store.get(submitted.mission_id)
    assert current is not None and current.task_id == resumed["record"].task_id
    assert current.execution_id == resumed["record"].execution_id
    assert current.state == "pending"
    assert current.completed_steps == ()


def test_stale_inventory_fallback_cannot_replace_resumed_mission(tmp_path: Path, monkeypatch) -> None:
    controller = MissionController(root=tmp_path)
    submitted = controller.submit("Inspect project status and summarize its structure.")
    resumed = {}

    def late_inventory(_role):
        controller._persist_recovered_items(controller.queue.recover_running())
        resumed["record"] = controller.resume(submitted.mission_id)
        return "PARTIAL RESULT — synthetic bounded inventory"

    monkeypatch.setattr(controller, "_read_only_project_status_report", late_inventory)
    controller.run_once()
    current = controller.store.get(submitted.mission_id)
    assert current is not None and current.task_id == resumed["record"].task_id
    assert current.state == "pending"


def test_recovery_during_memory_write_cannot_replace_new_mission_record(tmp_path: Path, monkeypatch) -> None:
    from autonomous_agent.execution_engine import ExecutionState

    controller = MissionController(root=tmp_path)
    submitted = controller.submit("inspect repository")
    monkeypatch.setattr(
        "autonomous_agent.mission_control.run_task",
        lambda *_args, **_kwargs: type("Result", (), {
            "state": ExecutionState.VERIFIED, "reason": "synthetic old result", "results": ()
        })(),
    )
    actual_record_episode = controller.memory.record_episode
    resumed = {}

    def switch_during_memory(*args, **kwargs):
        if kwargs.get("outcome") == "verified":
            controller._persist_recovered_items(controller.queue.recover_running())
            resumed["record"] = controller.resume(submitted.mission_id)
        return actual_record_episode(*args, **kwargs)

    monkeypatch.setattr(controller.memory, "record_episode", switch_during_memory)
    controller.run_once()
    current = controller.store.get(submitted.mission_id)
    assert current is not None and current.task_id == resumed["record"].task_id
    assert current.state == "pending"
    history = controller.memory.history(controller.MEMORY_PROJECT, kind="episode")
    by_execution = {
        entry.get("data", {}).get("metadata", {}).get("execution_id"): entry
        for entry in history
        if entry.get("data", {}).get("metadata", {}).get("mission_id") == submitted.mission_id
    }
    assert by_execution[current.execution_id]["outcome"] == "resume_queued"
    assert by_execution[submitted.execution_id]["outcome"] == "verified"
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from autonomous_agent.server import RuntimeHandler

    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.require_auth = True
    server.server_token = "synthetic-test-only-token"
    server.workspace_root = tmp_path
    server.mission_controller = controller
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/missions/{current.mission_id}/memory",
            headers={"X-Autonomous-Scout-Token": server.server_token},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            rows = json.loads(response.read())["memory"]
        assert len(rows) == 1 and rows[0]["outcome"] == "resume_queued"
        assert rows[0]["data"]["metadata"]["execution_id"] == current.execution_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_workspace_connector_denies_private_runtime_state_and_env_variants(tmp_path: Path) -> None:
    internal = tmp_path / "state" / "approvals" / "sample.json"
    internal.parent.mkdir(parents=True)
    internal.write_text('{"approval_token": "synthetic-sensitive-value"}', encoding="utf-8")
    dotenv = tmp_path / ".env.preview"
    dotenv.write_text("SERVER_TOKEN=synthetic-sensitive-value", encoding="utf-8")
    connector = WorkspaceConnector(tmp_path)
    assert "state" not in connector.list().entries
    assert ".env.preview" not in connector.list().entries
    for path in ("state", "state/approvals", "state/approvals/sample.json", ".env.preview"):
        with pytest.raises(WorkspaceError, match="authorized"):
            connector.read(path)
        with pytest.raises(WorkspaceError, match="authorized"):
            connector.list(path)
    with pytest.raises(WorkspaceError, match="authorized"):
        connector.write("state/approvals/another.json", "not a credential")


def test_workspace_connector_redacts_generic_and_json_form_tokens(tmp_path: Path) -> None:
    private = tmp_path / "facts.txt"
    private.write_text(
        'First fact. "approval_token": "synthetic-sensitive-value"\n'
        'Second fact. token=another-synthetic-value\n'
        'Authorization: Bearer third-synthetic-value\n',
        encoding="utf-8",
    )
    connector = WorkspaceConnector(tmp_path)
    result = connector.read("facts.txt")
    assert result.redacted
    assert "synthetic-sensitive-value" not in result.content
    assert "another-synthetic-value" not in result.content
    assert "third-synthetic-value" not in result.content
    assert "[REDACTED]" in result.content
    with pytest.raises(WorkspaceError, match="secret"):
        connector.write("unsafe.txt", "token=synthetic-sensitive-value")
