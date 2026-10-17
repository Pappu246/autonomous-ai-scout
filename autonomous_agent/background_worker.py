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
    """Durable queue consumer with bounded polling and per-attempt completion checks.

    A file/Blob lease is not a transactional side-effect fence across instances.
    """

    def __init__(
        self,
        queue: TaskQueueStore,
        handler: TaskHandler,
        *,
        poll_interval: float = 0.25,
        lease_store: ExecutionLeaseStore | None = None,
        recover_on_start: bool = True,
    ) -> None:
        if poll_interval < 0:
            raise ValueError("poll interval cannot be negative")
        self.queue = queue
        self.handler = handler
        self.poll_interval = poll_interval
        self.lease_store = lease_store
        self.recover_on_start = bool(recover_on_start)

    def recover(self) -> tuple[QueueItem, ...]:
        return self.queue.recover_running()

    def _finish_claim(self, item: QueueItem, *, success: bool, error: str) -> QueueItem:
        try:
            return self.queue.complete(
                item.task_id, success=success, error=error,
                expected_execution_id=item.execution_id, expected_attempt=item.attempts,
            )
        except ValueError:
            current = next(candidate for candidate in self.queue.list() if candidate.task_id == item.task_id)
            if (current.state is not QueueState.RUNNING
                    or current.execution_id != item.execution_id
                    or current.attempts != item.attempts):
                # Recovery/cancellation or a newer claim superseded this worker.
                # Never convert the newer attempt into this handler's outcome.
                return current
            raise

    def run_once(self) -> QueueItem | None:
        item = self.queue.claim_next()
        if item is None:
            return None
        lease = None
        if self.lease_store is not None:
            lease = self.lease_store.acquire(f"task:{item.task_id}", item.execution_id)
            if lease is None:
                return self._finish_claim(item, success=False, error="concurrency lease unavailable")
        try:
            try:
                success = bool(self.handler(item))
                error = "" if success else "handler returned false"
            except Exception as exc:
                success = False
                error = f"handler failed: {type(exc).__name__}"
            return self._finish_claim(item, success=success, error=error)
        finally:
            if lease is not None:
                self.lease_store.release(lease.lease_id)

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
        retry_delay = max(0.1, min(self.poll_interval if self.poll_interval > 0 else 0.5, 2.0))
        if self.recover_on_start:
            while not stop_event.is_set():
                try:
                    self.recover()
                    break
                except Exception as exc:
                    # Never let one transient shared-storage failure permanently
                    # kill the queue consumer. Log only the exception type.
                    print(
                        f"Background task worker startup recovery will retry: {type(exc).__name__}",
                        flush=True,
                    )
                    stop_event.wait(retry_delay)
        while not stop_event.is_set():
            try:
                if self.run_once() is None:
                    stop_event.wait(self.poll_interval)
            except Exception as exc:
                # Remote queue locks and storage can race across runtime
                # instances. Keep the worker alive and retry with bounded backoff.
                print(
                    f"Background task worker poll will retry: {type(exc).__name__}",
                    flush=True,
                )
                stop_event.wait(retry_delay)


__all__ = ["BackgroundTaskWorker", "TaskHandler", "WorkerResult"]
