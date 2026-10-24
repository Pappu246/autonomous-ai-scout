"""Execution-engine checkpoint adapter backed by a fenced PostgreSQL claim.

This adapter only replaces checkpoint persistence. It does not by itself migrate
mission rows, tool audit, run journal, approval state, or memory; hosted runtime
must remain paused until those other state paths are coordinated too.
"""
from __future__ import annotations

from .execution_checkpoint import ExecutionCheckpoint, SCHEMA_VERSION
from .postgres_coordinator import Claim, PostgresMissionCoordinator


class PostgresExecutionCheckpointStore:
    """Implement the execution checkpoint store contract for one active claim."""

    def __init__(self, coordinator: PostgresMissionCoordinator, claim: Claim) -> None:
        self._coordinator = coordinator
        self._claim = claim

    def _as_checkpoint(self, document: dict[str, object]) -> ExecutionCheckpoint:
        try:
            checkpoint = ExecutionCheckpoint(
                schema_version=int(document["schema_version"]),
                execution_id=str(document["execution_id"]),
                task_digest=str(document["task_digest"]),
                plan_digest=str(document["plan_digest"]),
                authorization_digest=str(document["authorization_digest"]),
                state=str(document["state"]),
                completed_step_ids=tuple(str(item) for item in document["completed_step_ids"]),
                total_attempts=int(document["total_attempts"]),
                updated_at=str(document["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("PostgreSQL execution checkpoint has an invalid schema") from exc
        if checkpoint.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported PostgreSQL execution checkpoint schema")
        if checkpoint.execution_id != self._claim.execution_id:
            raise ValueError("PostgreSQL checkpoint execution identity does not match the active claim")
        if checkpoint.total_attempts < 0:
            raise ValueError("PostgreSQL checkpoint attempts cannot be negative")
        return checkpoint

    def load(self) -> ExecutionCheckpoint | None:
        document = self._coordinator.load_checkpoint(self._claim)
        return None if document is None else self._as_checkpoint(document)

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
        if execution_id != self._claim.execution_id:
            raise ValueError("checkpoint execution identity does not match the active claim")
        document = self._coordinator.save_checkpoint(
            self._claim,
            task_digest=task_digest,
            plan_digest=plan_digest,
            authorization_digest=authorization_digest,
            state=state,
            completed_step_ids=completed_step_ids,
            total_attempts=total_attempts,
        )
        return self._as_checkpoint(document)


__all__ = ["PostgresExecutionCheckpointStore"]
