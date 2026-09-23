from __future__ import annotations
import json

from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from time import sleep
from pathlib import Path

import pytest

from autonomous_agent.background_worker import BackgroundTaskWorker
from autonomous_agent.task_queue import QueueState, TaskQueueStore


def test_queue_persists_enqueue_and_claim(tmp_path: Path):
    path = tmp_path / "queue.json"
    queue = TaskQueueStore(path)
    created = queue.enqueue("  inspect   repository ", task_id="task-1", execution_id="exec-1")
    assert created.state is QueueState.PENDING
    assert TaskQueueStore(path).list()[0].task == "inspect repository"

    claimed = TaskQueueStore(path).claim_next()
    assert claimed is not None
    assert claimed.state is QueueState.RUNNING
    assert claimed.attempts == 1


def test_queue_delays_claim_until_available_at(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1", available_at=future)
    assert queue.claim_next() is None


def test_worker_processes_tasks_without_caller_waiting(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1")
    seen: list[str] = []
    worker = BackgroundTaskWorker(queue, lambda item: seen.append(item.task) or True, poll_interval=0.01)
    stop = Event()
    thread = Thread(target=worker.run_forever, args=(stop,), daemon=True)
    thread.start()
    for _ in range(100):
        if TaskQueueStore(queue.path).list()[0].state is QueueState.SUCCEEDED:
            break
        sleep(0.01)
    stop.set()
    thread.join(timeout=1)
    assert seen == ["inspect repository"]
    assert TaskQueueStore(queue.path).list()[0].state is QueueState.SUCCEEDED


def test_worker_requeues_running_task_after_restart(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1")
    claimed = queue.claim_next()
    assert claimed is not None
    recovered = TaskQueueStore(queue.path).recover_running()
    assert recovered
    assert recovered[0].state is QueueState.RECOVERY_REQUIRED
    assert "explicit recovery confirmation required" in recovered[0].last_error
    assert queue.claim_next() is None
    confirmed = queue.confirm_recovery("task-1")
    assert confirmed.state is QueueState.PENDING


def test_worker_records_handler_failure(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1")
    worker = BackgroundTaskWorker(queue, lambda item: False)
    result = worker.run_once()
    assert result is not None
    assert result.state is QueueState.FAILED
    assert "handler returned false" in result.last_error


def test_queue_rejects_duplicate_task_id(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("inspect repository", task_id="same", execution_id="a")
    with pytest.raises(ValueError, match="already exists"):
        queue.enqueue("inspect repository", task_id="same", execution_id="b")


def test_queue_rejects_duplicate_persisted_task_ids(tmp_path: Path):
    path = tmp_path / "queue.json"
    item = {"task_id": "same", "task": "inspect repository", "execution_id": "exec-1", "state": "pending", "created_at": "2026-09-23T00:00:00+00:00", "updated_at": "2026-09-23T00:00:00+00:00", "available_at": "2026-09-23T00:00:00+00:00", "attempts": 0, "last_error": ""}
    duplicate = {**item, "execution_id": "exec-2"}
    path.write_text(json.dumps({"items": [item, duplicate]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate task_id"):
        TaskQueueStore(path).list()


def test_queued_task_redacts_secret_like_content(tmp_path: Path):
    path = tmp_path / "queue.json"
    queue = TaskQueueStore(path)
    item = queue.enqueue(
        "research credential=SUPERSECRET and token=ABC123",
        task_id="secret-task",
        execution_id="exec-1",
    )
    raw = path.read_text(encoding="utf-8")
    assert "SUPERSECRET" not in raw
    assert "ABC123" not in raw
    assert "[REDACTED]" in item.task


def test_queue_rejects_completion_of_non_running_task(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1")
    with pytest.raises(ValueError, match="running task"):
        queue.complete("task-1", success=True)


def test_queue_rejects_invalid_schedule(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    with pytest.raises(ValueError, match="available_at"):
        queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1", available_at="not-a-timestamp")


def test_queue_cannot_cancel_running_task(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("inspect repository", task_id="task-1", execution_id="exec-1")
    claimed = queue.claim_next()
    assert claimed is not None
    with pytest.raises(ValueError, match="only pending"):
        queue.cancel("task-1")
