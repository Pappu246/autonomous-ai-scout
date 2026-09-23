from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable


class DelegationError(ValueError):
    pass


@dataclass(frozen=True)
class DelegatedTask:
    task_id: str
    task: str
    capabilities: tuple[str, ...]
    parent_task_id: str
    depth: int
    approval_required: bool


@dataclass(frozen=True)
class DelegationPlan:
    parent_task_id: str
    children: tuple[DelegatedTask, ...]
    max_depth: int


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_delegation(
    parent_task_id: str,
    tasks: Iterable[tuple[str, Iterable[str]]],
    *,
    parent_capabilities: Iterable[str],
    parent_approved: bool,
    depth: int = 0,
    max_depth: int = 2,
    max_children: int = 8,
) -> DelegationPlan:
    parent_task_id = str(parent_task_id).strip()
    if not parent_task_id:
        raise DelegationError("parent task id is required")
    if depth < 0 or depth >= max_depth:
        raise DelegationError("delegation depth limit reached")
    if max_children < 1 or max_children > 16:
        raise DelegationError("child count limit is invalid")
    allowed = {str(item).strip() for item in parent_capabilities if str(item).strip()}
    children: list[DelegatedTask] = []
    for index, (task, capabilities) in enumerate(tasks):
        if index >= max_children:
            raise DelegationError("delegation child limit exceeded")
        normalized_task = " ".join(str(task).split())
        if not normalized_task or len(normalized_task) > 4000:
            raise DelegationError("delegated task is invalid")
        requested = tuple(sorted({str(item).strip() for item in capabilities if str(item).strip()}))
        if not set(requested).issubset(allowed):
            raise DelegationError("child capability exceeds parent capability scope")
        write = any(item.endswith(".write") or item.endswith(".send") or "change" in item for item in requested)
        approval_required = bool(write)
        if approval_required and not parent_approved:
            raise DelegationError("write-capable delegation requires parent approval")
        task_id = _digest((parent_task_id, index, normalized_task, requested))[:32]
        children.append(DelegatedTask(task_id, normalized_task, requested, parent_task_id, depth + 1, approval_required))
    return DelegationPlan(parent_task_id, tuple(children), max_depth)


__all__ = ["DelegatedTask", "DelegationError", "DelegationPlan", "build_delegation"]
