from __future__ import annotations

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
    assert recovered[0].state is QueueState.PENDING
    assert recovered[0].last_error == "worker restarted while task was running"


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
