from __future__ import annotations

from dataclasses import dataclass

from .task_queue import QueueItem, TaskQueueStore
from .triggers import TriggerRegistry


@dataclass(frozen=True)
class TriggerDispatch:
    trigger_id: str
    task_id: str
    queued: bool
    already_queued: bool
    item: QueueItem | None


class TriggerQueueDispatcher:
    """Bridge trigger events into the durable queue without executing work."""

    def __init__(self, registry: TriggerRegistry, queue: TaskQueueStore) -> None:
        self.registry = registry
        self.queue = queue

    def dispatch(
        self,
        trigger_id: str,
        *,
        task: str,
        task_id: str,
        execution_id: str,
        available_at: str | None = None,
    ) -> TriggerDispatch:
        trigger_id = str(trigger_id)
        task_id = str(task_id).strip()
        if not task_id:
            raise ValueError("task_id is required")
        if not self.registry.ready(trigger_id):
            return TriggerDispatch(trigger_id, task_id, False, False, None)

        already_queued = False
        try:
            item = self.queue.enqueue(
                task,
                task_id=task_id,
                execution_id=execution_id,
                available_at=available_at,
            )
        except ValueError as exc:
            if "task_id already exists" not in str(exc):
                raise
            already_queued = True
            item = next(
                (candidate for candidate in self.queue.list() if candidate.task_id == task_id),
                None,
            )
            if item is None:
                raise

        if self.registry.fire(trigger_id):
            return TriggerDispatch(trigger_id, task_id, True, already_queued, item)

        if not already_queued and item is not None and item.state.value == "pending":
            self.queue.cancel(task_id)
        return TriggerDispatch(trigger_id, task_id, False, already_queued, item)


__all__ = ["TriggerDispatch", "TriggerQueueDispatcher"]
