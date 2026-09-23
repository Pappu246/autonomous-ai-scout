from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ExecutionCheckpoint:
    schema_version: int
    execution_id: str
    task_digest: str
    plan_digest: str
    authorization_digest: str
    state: str
    completed_step_ids: tuple[str, ...]
    total_attempts: int
    updated_at: str

    @property
    def next_step_index(self) -> int:
        return len(self.completed_step_ids)


class ExecutionCheckpointStore:
    """Atomic durable checkpoint store containing execution metadata only."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> ExecutionCheckpoint | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("execution checkpoint is unreadable") from exc
        if not isinstance(payload, dict):
            raise ValueError("execution checkpoint must be a JSON object")
        try:
            completed = tuple(str(item) for item in payload["completed_step_ids"])
            checkpoint = ExecutionCheckpoint(
                int(payload["schema_version"]),
                str(payload["execution_id"]),
                str(payload["task_digest"]),
                str(payload["plan_digest"]),
                str(payload["authorization_digest"]),
                str(payload["state"]),
                completed,
                int(payload["total_attempts"]),
                str(payload["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("execution checkpoint has an invalid schema") from exc
        if checkpoint.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported execution checkpoint schema")
        if checkpoint.total_attempts < 0:
            raise ValueError("execution checkpoint attempts cannot be negative")
        return checkpoint

    def save(
        self,
        *,
        execution_id: str,
        task_digest: str,
        plan_digest: str,
        authorization_digest: str,
        state: str,
        completed_step_ids: tuple[str, ...],
        total_attempts: int,
    ) -> ExecutionCheckpoint:
        checkpoint = ExecutionCheckpoint(
            SCHEMA_VERSION,
            execution_id,
            task_digest,
            plan_digest,
            authorization_digest,
            state,
            tuple(dict.fromkeys(completed_step_ids)),
            max(0, int(total_attempts)),
            datetime.now(timezone.utc).isoformat(),
        )
        payload = {
            "completed_step_ids": list(checkpoint.completed_step_ids),
            "execution_id": checkpoint.execution_id,
            "plan_digest": checkpoint.plan_digest,
            "authorization_digest": checkpoint.authorization_digest,
            "schema_version": checkpoint.schema_version,
            "state": checkpoint.state,
            "task_digest": checkpoint.task_digest,
            "total_attempts": checkpoint.total_attempts,
            "updated_at": checkpoint.updated_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        return checkpoint


__all__ = ["ExecutionCheckpoint", "ExecutionCheckpointStore", "SCHEMA_VERSION"]
