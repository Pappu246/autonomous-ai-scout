from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from time import monotonic
from typing import Callable

from .concurrency import ExecutionLeaseStore
from .task_queue import QueueItem, QueueState, TaskQueueStore


@dataclass(frozen=True)
class WorkerResult:
    processed: int
    succeeded: int
    failed: int


TaskHandler = Callable[[QueueItem], bool]


class BackgroundTaskWorker:
    """Single-worker durable queue consumer with bounded polling."""

    def __init__(
        self,
        queue: TaskQueueStore,
        handler: TaskHandler,
        *,
        poll_interval: float = 0.25,
        lease_store: ExecutionLeaseStore | None = None,
    ) -> None:
        if poll_interval < 0:
            raise ValueError("poll interval cannot be negative")
        self.queue = queue
        self.handler = handler
        self.poll_interval = poll_interval
        self.lease_store = lease_store

    def recover(self) -> tuple[QueueItem, ...]:
        return self.queue.recover_running()

    def run_once(self) -> QueueItem | None:
        item = self.queue.claim_next()
        if item is None:
            return None
        lease = None
        if self.lease_store is not None:
            lease = self.lease_store.acquire(f"task:{item.task_id}", item.execution_id)
            if lease is None:
                self.queue.complete(item.task_id, success=False, error="concurrency lease unavailable")
                return next(candidate for candidate in self.queue.list() if candidate.task_id == item.task_id)
        try:
            success = bool(self.handler(item))
            self.queue.complete(item.task_id, success=success, error="" if success else "handler returned false")
        except Exception as exc:
            self.queue.complete(item.task_id, success=False, error=f"handler failed: {type(exc).__name__}")
        finally:
            if lease is not None:
                self.lease_store.release(lease.lease_id)
        return next(candidate for candidate in self.queue.list() if candidate.task_id == item.task_id)

    def run_until_empty(self, *, max_items: int = 100) -> WorkerResult:
        if max_items < 0:
            raise ValueError("max_items cannot be negative")
        self.recover()
        processed = succeeded = failed = 0
        while processed < max_items:
            item = self.run_once()
            if item is None:
                break
            processed += 1
            if item.state is QueueState.SUCCEEDED:
                succeeded += 1
            else:
                failed += 1
        return WorkerResult(processed, succeeded, failed)

    def run_forever(self, stop_event: Event) -> None:
        self.recover()
        while not stop_event.is_set():
            if self.run_once() is None:
                stop_event.wait(self.poll_interval)


__all__ = ["BackgroundTaskWorker", "TaskHandler", "WorkerResult"]
