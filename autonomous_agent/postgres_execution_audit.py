"""Claim-fenced PostgreSQL audit store for the execution engine."""
from __future__ import annotations

import uuid
from collections.abc import Mapping

from .postgres_coordinator import CoordinatorUnavailable, Claim, PostgresMissionCoordinator

_MAX_TIMELINE_EVENTS = 20_000


class PostgresExecutionAuditStore:
    """Hash-chained audit backend bound to one current PostgreSQL worker claim.

    This adapter only handles execution audit. A caller must still coordinate
    mission state, run journal, approvals, memory, and other checkpoint paths.
    """

    def __init__(self, coordinator: PostgresMissionCoordinator, claim: Claim) -> None:
        self._coordinator = coordinator
        self._claim = claim

    def _belongs_to_claim(self, execution_id: str) -> bool:
        return bool(
            isinstance(execution_id, str)
            and (execution_id == self._claim.execution_id
                 or execution_id.startswith(self._claim.execution_id + ":"))
        )

    def verify(self) -> bool:
        try:
            self._coordinator.timeline(self._claim.mission_id, limit=_MAX_TIMELINE_EVENTS)
        except CoordinatorUnavailable:
            return False
        return True

    def append(self, record: Mapping[str, str]) -> object:
        if not isinstance(record, Mapping):
            raise ValueError("execution audit record must be an object")
        execution_id = str(record.get("execution_id", ""))
        if not self._belongs_to_claim(execution_id):
            raise ValueError("execution evidence does not belong to the active claim")
        event_kind = str(record.get("event", "execution_event") or "execution_event")
        payload = {
            str(key): str(value)
            for key, value in record.items()
            if key not in {"execution_id", "event", "hash"}
        }
        # A failed/ambiguous commit is propagated. Do not retry an event with
        # a new identity or execute the next tool on uncertain state.
        return self._coordinator.append_claim_event(
            self._claim,
            "execution-event-" + uuid.uuid4().hex,
            event_kind,
            execution_id,
            payload,
        )

    def events(self, execution_id: str) -> tuple[dict[str, str], ...]:
        if not self._belongs_to_claim(execution_id):
            raise ValueError("execution evidence does not belong to the active claim")
        events = self._coordinator.timeline(self._claim.mission_id, limit=_MAX_TIMELINE_EVENTS)
        matching: list[dict[str, str]] = []
        for event in events:
            if event.execution_id != execution_id:
                continue
            record = {str(key): str(value) for key, value in event.payload.items()}
            record["execution_id"] = event.execution_id
            record["event"] = event.kind
            record["audit_event_id"] = event.event_id
            record["audit_sequence"] = str(event.sequence)
            matching.append(record)
        return tuple(matching)


__all__ = ["PostgresExecutionAuditStore"]
