"""Transactional PostgreSQL mission-state adapter (not a hosted rollout switch).

This adapter is intentionally independent of the legacy JSON file stores. The
file-backed MissionController still pauses on Vercel: enabling this adapter
requires an approved schema, a Preview-only connection, migration of *all*
checkpoint/audit/approval/memory call sites, and live fault testing. No DSN is
read from the environment here and constructing the adapter never provisions a
service. A database transaction, not a Blob GET/delete lease, owns each change.

The adapter does not make external tool side effects exactly once. Expired
running work becomes recovery_required, NEVER automatically pending again.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Mapping

import psycopg
from psycopg.types.json import Jsonb


SCHEMA_VERSION = 1
_MAX_DOCUMENT_BYTES = 1_048_576
_MAX_AUDIT_EVENTS = 20_000
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_TERMINAL = frozenset({"verified", "partial", "failed"})


class CoordinatorUnavailable(RuntimeError):
    """No safe DB transaction was available; never use local/Blob fallback."""


class CommitOutcomeUnknown(CoordinatorUnavailable):
    """COMMIT acknowledgement was lost; reconcile by identity, never replay."""


class StaleGeneration(ValueError):
    """An older claim, revision or approval can no longer mutate this state."""


class IdentityConflict(ValueError):
    """An idempotency key/event ID was reused with different content."""


def _id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a bounded, non-secret identifier")
    return value


def _json(value: Mapping[str, object], name: str) -> tuple[dict[str, object], bytes]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    try:
        payload = dict(value)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                               allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON serializable") from exc
    if len(canonical) > _MAX_DOCUMENT_BYTES:
        raise ValueError(f"{name} exceeds the state size limit")
    return payload, canonical


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


@dataclass(frozen=True)
class Submission:
    mission_id: str
    task_id: str
    execution_id: str
    duplicated: bool


@dataclass(frozen=True)
class Claim:
    mission_id: str
    task_id: str
    execution_id: str
    generation: int
    worker_id: str
    payload: dict[str, object]


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    event_id: str
    kind: str
    execution_id: str
    previous_hash: str
    event_hash: str
    payload: dict[str, object]


class PostgresMissionCoordinator:
    """One PostgreSQL database; row-locked, per-namespace state transitions.

    The migration is an owner-only operation. Callers must use a restricted
    runtime role (no DDL/DELETE/audit UPDATE), a distinct Preview namespace,
    and an approved connection pool with transaction pooling *disabled* for
    these short-lived transactions. Each operation opens its own connection:
    there are no time-expiring locks, unlock-by-owner GETs, or local fallback.
    """

    def __init__(self, dsn: str, namespace: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("an approved PostgreSQL connection is required")
        self._dsn = dsn
        self.namespace = _id(namespace, "namespace")

    def _connect(self):
        try:
            return psycopg.connect(
                self._dsn, connect_timeout=5,
                options="-c lock_timeout=5000 -c statement_timeout=15000 -c idle_in_transaction_session_timeout=20000",
            )
        except psycopg.Error:
            # Do not expose a DSN or provider diagnostics in application logs.
            raise CoordinatorUnavailable("PostgreSQL coordinator is unavailable") from None

    def _commit(self, connection) -> None:
        connection.commit()

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection]:
        connection = self._connect()
        try:
            try:
                yield connection
            except BaseException as exc:
                try:
                    connection.rollback()
                except psycopg.Error:
                    pass
                if isinstance(exc, psycopg.Error):
                    raise CoordinatorUnavailable("PostgreSQL state transaction failed") from None
                raise
            try:
                self._commit(connection)
            except psycopg.Error:
                # A server may have committed before the acknowledgement was
                # lost. Do not rerun a tool, approval or arbitrary mutation.
                raise CommitOutcomeUnknown("state commit outcome unknown; reconcile by stable ID") from None
        finally:
            try:
                connection.close()
            except psycopg.Error:
                pass

    def check_ready(self) -> None:
        """Read-only schema check. Does not create a table or enable hosted work."""
        with self._transaction() as conn:
            row = conn.execute("SELECT version FROM scout_state.schema_version WHERE singleton = 1").fetchone()
            if row is None or row[0] != SCHEMA_VERSION:
                raise CoordinatorUnavailable("unsupported coordinator schema version")
            for name in ("submissions", "missions", "work_items", "approvals", "audit_heads",
                         "audit_events", "memories"):
                if conn.execute("SELECT to_regclass(%s)", ("scout_state." + name,)).fetchone()[0] is None:
                    raise CoordinatorUnavailable("coordinator schema is incomplete")

    def submit(
        self, idempotency_key: str, request: Mapping[str, object],
        mission: Mapping[str, object], queue: Mapping[str, object],
        *, requires_output: bool = False, requires_approval: bool = False,
    ) -> Submission:
        """Atomically create mission + pending work, or return the prior ID.

        An idempotency key is generated by the *caller*, not derived from task
        text. Conflicting payloads using the same key are never accepted.
        Approval-gated submissions have no runnable queue item until consumed.
        """
        key = _id(idempotency_key, "idempotency key")
        request_data, request_bytes = _json(request, "request")
        for name in ("authorization_digest", "criteria_digest"):
            if not isinstance(request_data.get(name), str) or not _HEX_DIGEST.fullmatch(request_data[name]):
                raise ValueError(f"immutable request needs a {name}")
        mission_data, _ = _json(mission, "mission")
        queue_data, _ = _json(queue, "queue item")
        # An idempotent retry must not silently change the authorization or
        # approval requirements while retaining the same task text and key.
        digest = hashlib.sha256(json.dumps(
            [hashlib.sha256(request_bytes).hexdigest(), bool(requires_output), bool(requires_approval)],
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        mission_id = "mission-" + uuid.uuid4().hex
        task_id = mission_id
        execution_id = "exec-" + uuid.uuid4().hex
        with self._transaction() as conn:
            row = conn.execute(
                """INSERT INTO scout_state.submissions
                   (namespace, idempotency_key, request_digest, mission_id, task_id, execution_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (namespace, idempotency_key) DO NOTHING
                   RETURNING mission_id""",
                (self.namespace, key, digest, mission_id, task_id, execution_id),
            ).fetchone()
            if row is None:
                existing = conn.execute(
                    """SELECT request_digest, mission_id, task_id, execution_id
                       FROM scout_state.submissions WHERE namespace = %s AND idempotency_key = %s""",
                    (self.namespace, key),
                ).fetchone()
                if existing is None or existing[0] != digest:
                    raise IdentityConflict("submission ID was reused with different content")
                mission_exists = conn.execute(
                    "SELECT 1 FROM scout_state.missions WHERE namespace = %s AND mission_id = %s",
                    (self.namespace, existing[1]),
                ).fetchone()
                if mission_exists is None:
                    raise CoordinatorUnavailable("submitted mission is incomplete; reconcile before retry")
                return Submission(existing[1], existing[2], existing[3], True)
            conn.execute(
                """INSERT INTO scout_state.missions
                   (namespace, mission_id, task_id, execution_id, state, requires_output, payload)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (self.namespace, mission_id, task_id, execution_id,
                 "requires_approval" if requires_approval else "pending", bool(requires_output), Jsonb(mission_data)),
            )
            if not requires_approval:
                conn.execute(
                    """INSERT INTO scout_state.work_items
                       (namespace, task_id, mission_id, execution_id, state, payload)
                       VALUES (%s, %s, %s, %s, 'pending', %s)""",
                    (self.namespace, task_id, mission_id, execution_id, Jsonb(queue_data)),
                )
            return Submission(mission_id, task_id, execution_id, False)

    def claim_next(self, worker_id: str) -> Claim | None:
        """SKIP LOCKED + increasing generation; no TTL-based delete/takeover."""
        worker = _id(worker_id, "worker ID")
        with self._transaction() as conn:
            row = conn.execute(
                """SELECT q.task_id, q.mission_id, q.execution_id, q.claim_generation, q.payload
                   FROM scout_state.work_items AS q
                   JOIN scout_state.missions AS m
                     ON m.namespace = q.namespace AND m.mission_id = q.mission_id
                    AND m.task_id = q.task_id AND m.execution_id = q.execution_id
                   WHERE q.namespace = %s AND q.state = 'pending' AND m.state = 'pending'
                     AND q.available_at <= clock_timestamp()
                   ORDER BY q.available_at, q.task_id
                   FOR UPDATE OF q SKIP LOCKED LIMIT 1""",
                (self.namespace,),
            ).fetchone()
            if row is None:
                return None
            task_id, mission_id, execution_id, old_generation, payload = row
            generation = old_generation + 1
            updated = conn.execute(
                """UPDATE scout_state.missions SET state = 'running', claim_generation = %s,
                   revision = revision + 1, updated_at = clock_timestamp()
                   WHERE namespace = %s AND mission_id = %s AND task_id = %s
                     AND execution_id = %s AND state = 'pending' AND claim_generation < %s
                   RETURNING mission_id""",
                (generation, self.namespace, mission_id, task_id, execution_id, generation),
            ).fetchone()
            if updated is None:
                raise StaleGeneration("mission changed before the queue claim")
            conn.execute(
                """UPDATE scout_state.work_items SET state = 'running', claim_generation = %s,
                   worker_id = %s, heartbeat_at = clock_timestamp(), updated_at = clock_timestamp()
                   WHERE namespace = %s AND task_id = %s""",
                (generation, worker, self.namespace, task_id),
            )
            return Claim(mission_id, task_id, execution_id, generation, worker, payload)

    def heartbeat(self, claim: Claim) -> None:
        with self._transaction() as conn:
            row = conn.execute(
                """UPDATE scout_state.work_items SET heartbeat_at = clock_timestamp()
                   WHERE namespace = %s AND task_id = %s AND execution_id = %s
                     AND claim_generation = %s AND worker_id = %s AND state = 'running'
                   RETURNING task_id""",
                (self.namespace, claim.task_id, claim.execution_id, claim.generation, claim.worker_id),
            ).fetchone()
            if row is None:
                raise StaleGeneration("worker claim is no longer current")

    def _append_event(self, conn, stream_id: str, event_id: str, kind: str,
                      execution_id: str, payload: Mapping[str, object]) -> AuditEvent:
        stream = _id(stream_id, "audit stream")
        event = _id(event_id, "event ID")
        event_kind = _id(kind, "event kind")
        execution = _id(execution_id, "execution ID")
        document, _ = _json(payload, "audit event")
        if conn.execute(
            "SELECT 1 FROM scout_state.missions WHERE namespace = %s AND mission_id = %s",
            (self.namespace, stream),
        ).fetchone() is None:
            raise ValueError("audit event must belong to a persisted mission")
        # Serializes identical event IDs even across streams. Hash collisions
        # cause extra waiting only; the unique constraint remains authoritative.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                     (self.namespace + ":" + event,))
        prior = conn.execute(
            """SELECT stream_id, sequence, event_kind, execution_id, previous_hash, event_hash, payload
               FROM scout_state.audit_events WHERE namespace = %s AND event_id = %s""",
            (self.namespace, event),
        ).fetchone()
        if prior is not None:
            if prior[0] != stream or prior[2] != event_kind or prior[3] != execution or prior[6] != document:
                raise IdentityConflict("audit event ID was reused with different content")
            return AuditEvent(prior[1], event, event_kind, execution, prior[4], prior[5], prior[6])
        conn.execute(
            """INSERT INTO scout_state.audit_heads (namespace, stream_id) VALUES (%s, %s)
               ON CONFLICT (namespace, stream_id) DO NOTHING""", (self.namespace, stream),
        )
        sequence, previous = conn.execute(
            """SELECT sequence, event_hash FROM scout_state.audit_heads
               WHERE namespace = %s AND stream_id = %s FOR UPDATE""",
            (self.namespace, stream),
        ).fetchone()
        sequence += 1
        sealed = json.dumps([self.namespace, stream, sequence, event, event_kind, execution,
                             previous, document], sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True).encode("utf-8")
        event_hash = hashlib.sha256(sealed).hexdigest()
        conn.execute(
            """INSERT INTO scout_state.audit_events
               (namespace, stream_id, sequence, event_id, event_kind, execution_id,
                previous_hash, event_hash, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (self.namespace, stream, sequence, event, event_kind, execution,
             previous, event_hash, Jsonb(document)),
        )
        conn.execute(
            """UPDATE scout_state.audit_heads SET sequence = %s, event_hash = %s
               WHERE namespace = %s AND stream_id = %s""",
            (sequence, event_hash, self.namespace, stream),
        )
        return AuditEvent(sequence, event, event_kind, execution, previous, event_hash, document)

    def append_event(self, stream_id: str, event_id: str, kind: str,
                     execution_id: str, payload: Mapping[str, object]) -> AuditEvent:
        with self._transaction() as conn:
            return self._append_event(conn, stream_id, event_id, kind, execution_id, payload)

    def timeline(self, stream_id: str, *, limit: int = 500) -> tuple[AuditEvent, ...]:
        if not 1 <= limit <= _MAX_AUDIT_EVENTS:
            raise ValueError("timeline limit is out of bounds")
        stream = _id(stream_id, "audit stream")
        with self._transaction() as conn:
            # One MVCC snapshot: do not mistake a concurrent append for a
            # corrupt audit head while verifying the whole bounded chain.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = conn.execute(
                """SELECT sequence, event_id, event_kind, execution_id, previous_hash, event_hash, payload
                   FROM scout_state.audit_events WHERE namespace = %s AND stream_id = %s
                   ORDER BY sequence LIMIT %s""", (self.namespace, stream, _MAX_AUDIT_EVENTS + 1),
            ).fetchall()
            head = conn.execute(
                """SELECT sequence, event_hash FROM scout_state.audit_heads
                   WHERE namespace = %s AND stream_id = %s""", (self.namespace, stream),
            ).fetchone()
        if len(rows) > _MAX_AUDIT_EVENTS:
            raise CoordinatorUnavailable("audit stream exceeds the bounded verifier; archive under review")
        result: list[AuditEvent] = []
        previous = ""
        for sequence, event_id, kind, execution, prior, saved_hash, payload in rows:
            if sequence != len(result) + 1 or prior != previous:
                raise CoordinatorUnavailable("audit sequence is corrupt")
            sealed = json.dumps([self.namespace, stream, sequence, event_id, kind, execution,
                                 prior, payload], sort_keys=True, separators=(",", ":"),
                                ensure_ascii=True).encode("utf-8")
            if hashlib.sha256(sealed).hexdigest() != saved_hash:
                raise CoordinatorUnavailable("audit hash chain is corrupt")
            result.append(AuditEvent(sequence, event_id, kind, execution, prior, saved_hash, payload))
            previous = saved_hash
        if (head is None and rows) or (head is not None and head != (len(result), previous)):
            raise CoordinatorUnavailable("audit head disagrees with committed event chain")
        return tuple(result[-limit:])

    def recover_stale(self, *, older_than_seconds: int, max_items: int = 50) -> tuple[str, ...]:
        """Fence stale workers, never replay running side effects automatically."""
        if not 30 <= older_than_seconds <= 86_400 or not 1 <= max_items <= 100:
            raise ValueError("recovery bounds are invalid")
        recovered: list[str] = []
        with self._transaction() as conn:
            rows = conn.execute(
                """SELECT task_id, mission_id, execution_id, claim_generation
                   FROM scout_state.work_items
                   WHERE namespace = %s AND state = 'running'
                     AND heartbeat_at < clock_timestamp() - make_interval(secs => %s)
                   ORDER BY heartbeat_at, task_id
                   FOR UPDATE SKIP LOCKED LIMIT %s""",
                (self.namespace, older_than_seconds, max_items),
            ).fetchall()
            for task_id, mission_id, execution_id, generation in rows:
                next_generation = generation + 1
                conn.execute(
                    """UPDATE scout_state.work_items SET state = 'recovery_required',
                       worker_id = NULL, claim_generation = %s, updated_at = clock_timestamp()
                       WHERE namespace = %s AND task_id = %s""",
                    (next_generation, self.namespace, task_id),
                )
                row = conn.execute(
                    """UPDATE scout_state.missions SET state = 'recovery_required',
                       claim_generation = %s, revision = revision + 1,
                       updated_at = clock_timestamp()
                       WHERE namespace = %s AND mission_id = %s AND execution_id = %s
                         AND claim_generation = %s AND state = 'running'
                       RETURNING mission_id""",
                    (next_generation, self.namespace, mission_id, execution_id, generation),
                ).fetchone()
                if row is None:
                    raise StaleGeneration("mission no longer matches recovering worker")
                self._append_event(conn, mission_id, f"recovery:{task_id}:{next_generation}",
                                   "recovery_required", execution_id,
                                   {"reason": "heartbeat expired; manual reconciliation required"})
                recovered.append(task_id)
        return tuple(recovered)

    def finish(self, claim: Claim, state: str, *, validated_output: str = "",
               validation_event_id: str | None = None,
               memory: Mapping[str, object] | None = None) -> None:
        """Atomically fence queue+mission+terminal audit+memory.

        For requests requiring an artifact, VERIFIED requires an independently
        persisted output_validated event with matching digest. The caller must
        still run the existing output validator and independent tool/approval
        checks; this method cannot validate arbitrary external side effects.
        """
        if state not in _TERMINAL:
            raise ValueError("invalid terminal state")
        if state != "verified" and (validated_output or validation_event_id or memory is not None):
            raise ValueError("unverified work cannot publish validated output or memory")
        if len(validated_output) > 8_192:
            raise ValueError("validated output exceeds the size limit")
        with self._transaction() as conn:
            row = conn.execute(
                """SELECT execution_id, claim_generation, worker_id, state
                   FROM scout_state.work_items WHERE namespace = %s AND task_id = %s FOR UPDATE""",
                (self.namespace, claim.task_id),
            ).fetchone()
            if row != (claim.execution_id, claim.generation, claim.worker_id, "running"):
                raise StaleGeneration("queue claim was superseded")
            mission = conn.execute(
                """SELECT task_id, execution_id, claim_generation, state, requires_output
                   FROM scout_state.missions WHERE namespace = %s AND mission_id = %s FOR UPDATE""",
                (self.namespace, claim.mission_id),
            ).fetchone()
            if mission is None or mission[:4] != (claim.task_id, claim.execution_id, claim.generation, "running"):
                raise StaleGeneration("mission claim was superseded")
            if state == "verified":
                if not validation_event_id:
                    raise ValueError("VERIFIED requires a validated result event")
                evidence = conn.execute(
                    """SELECT stream_id, event_kind, execution_id, payload
                       FROM scout_state.audit_events WHERE namespace = %s AND event_id = %s""",
                    (self.namespace, _id(validation_event_id, "validation event ID")),
                ).fetchone()
                if mission[4]:
                    if (not validated_output or evidence is None
                            or evidence[:3] != (claim.mission_id, "output_validated", claim.execution_id)
                            or evidence[3].get("output_sha256") != _digest(validated_output)):
                        raise ValueError("requested output lacks matching validated audit evidence")
                elif (validated_output or evidence is None
                      or evidence[:3] != (claim.mission_id, "tool_result", claim.execution_id)
                      or evidence[3].get("verification") != "verified"
                      or evidence[3].get("result") != "success"):
                    raise ValueError("tool result lacks matching validated audit evidence")
            conn.execute(
                """UPDATE scout_state.work_items SET state = %s, worker_id = NULL,
                   updated_at = clock_timestamp() WHERE namespace = %s AND task_id = %s""",
                (state, self.namespace, claim.task_id),
            )
            conn.execute(
                """UPDATE scout_state.missions SET state = %s, revision = revision + 1,
                   updated_at = clock_timestamp(),
                   payload = jsonb_set(payload, '{output}', to_jsonb(%s::text), true)
                   WHERE namespace = %s AND mission_id = %s""",
                (state, validated_output, self.namespace, claim.mission_id),
            )
            self._append_event(
                conn, claim.mission_id, f"terminal:{claim.task_id}:{claim.generation}",
                "mission_state", claim.execution_id,
                {"state": state, "output_sha256": _digest(validated_output) if validated_output else ""},
            )
            if memory is not None:
                document, _ = _json(memory, "memory")
                if not validation_event_id or state != "verified":
                    raise ValueError("verified memory requires validated audit evidence")
                conn.execute(
                    """INSERT INTO scout_state.memories
                       (namespace, mission_id, execution_id, kind, outcome, source_event_id, payload)
                       VALUES (%s, %s, %s, 'episode', 'verified', %s, %s)""",
                    (self.namespace, claim.mission_id, claim.execution_id,
                     validation_event_id, Jsonb(document)),
                )
            # The terminal event is committed with the state transition.

    def propose_approval(self, action_id: str, mission_id: str,
                         expires_at: datetime, payload: Mapping[str, object]) -> None:
        """Persist a proposal only for a mission already blocked on approval."""
        action = _id(action_id, "approval action")
        mission = _id(mission_id, "mission ID")
        document, _ = _json(payload, "approval proposal")
        with self._transaction() as conn:
            state = conn.execute(
                """SELECT state FROM scout_state.missions
                   WHERE namespace = %s AND mission_id = %s FOR UPDATE""",
                (self.namespace, mission),
            ).fetchone()
            if state != ("requires_approval",):
                raise ValueError("mission is not awaiting approval")
            conn.execute(
                """INSERT INTO scout_state.approvals
                   (namespace, action_id, mission_id, state, expires_at, payload)
                   VALUES (%s, %s, %s, 'pending', %s, %s)""",
                (self.namespace, action, mission, _aware(expires_at, "expiry"), Jsonb(document)),
            )

    def decide_approval(self, action_id: str, decision: str, *, approval_token: str | None = None) -> None:
        """Only the authenticated, independently authorized API may call this."""
        if decision not in {"approved", "rejected"}:
            raise ValueError("approval decision is invalid")
        if decision == "approved" and (not isinstance(approval_token, str) or len(approval_token) < 32):
            raise ValueError("approved action needs a high-entropy token")
        if decision == "rejected" and approval_token is not None:
            raise ValueError("rejection must not carry an approval token")
        action = _id(action_id, "approval action")
        digest = _digest(approval_token) if approval_token is not None else None
        with self._transaction() as conn:
            updated = conn.execute(
                """UPDATE scout_state.approvals SET state = %s, token_digest = %s
                   WHERE namespace = %s AND action_id = %s AND state = 'pending'
                     AND expires_at > clock_timestamp() RETURNING mission_id""",
                (decision, digest, self.namespace, action),
            ).fetchone()
            if updated is None:
                raise StaleGeneration("approval is missing, expired or already decided")
            self._append_event(conn, updated[0], f"approval_decision:{action}",
                               "approval_decision", "approval-" + action, {"decision": decision})

    def activate_approved(self, action_id: str, approval_token: str) -> bool:
        """Consume once and enqueue in the same transaction; no tool execution.

        A crash after COMMIT but before the response leaves *one* pending item.
        A repeat cannot consume the approval again. The actual side effect
        still requires the existing independent policy/approval checks.
        """
        action = _id(action_id, "approval action")
        if not isinstance(approval_token, str) or not approval_token:
            return False
        with self._transaction() as conn:
            row = conn.execute(
                """UPDATE scout_state.approvals SET state = 'consumed',
                   consumed_at = clock_timestamp()
                   WHERE namespace = %s AND action_id = %s AND state = 'approved'
                     AND token_digest = %s AND expires_at > clock_timestamp()
                   RETURNING mission_id""",
                (self.namespace, action, _digest(approval_token)),
            ).fetchone()
            if row is None:
                return False
            mission_id = row[0]
            mission = conn.execute(
                """UPDATE scout_state.missions SET state = 'pending', revision = revision + 1,
                   updated_at = clock_timestamp()
                   WHERE namespace = %s AND mission_id = %s AND state = 'requires_approval'
                   RETURNING task_id, execution_id""",
                (self.namespace, mission_id),
            ).fetchone()
            if mission is None:
                raise StaleGeneration("approved mission is no longer actionable")
            conn.execute(
                """INSERT INTO scout_state.work_items
                   (namespace, task_id, mission_id, execution_id, state, payload)
                   VALUES (%s, %s, %s, %s, 'pending', '{}'::jsonb)""",
                (self.namespace, mission[0], mission_id, mission[1]),
            )
            self._append_event(conn, mission_id, f"approval_consumed:{action}",
                               "approval_consumed", mission[1], {"action_id": action})
            return True

    def mission(self, mission_id: str) -> dict[str, object] | None:
        with self._transaction() as conn:
            row = conn.execute(
                """SELECT state, revision, claim_generation, payload
                   FROM scout_state.missions WHERE namespace = %s AND mission_id = %s""",
                (self.namespace, _id(mission_id, "mission ID")),
            ).fetchone()
            if row is None:
                return None
            return {"state": row[0], "revision": row[1], "generation": row[2], "payload": row[3]}

    def memory(self, mission_id: str) -> tuple[dict[str, object], ...]:
        with self._transaction() as conn:
            rows = conn.execute(
                """SELECT execution_id, kind, outcome, source_event_id, payload
                   FROM scout_state.memories WHERE namespace = %s AND mission_id = %s
                   ORDER BY created_at, execution_id LIMIT 500""",
                (self.namespace, _id(mission_id, "mission ID")),
            ).fetchall()
            return tuple({"execution_id": row[0], "kind": row[1], "outcome": row[2],
                          "source_event_id": row[3], "payload": row[4]} for row in rows)


__all__ = [
    "AuditEvent", "Claim", "CommitOutcomeUnknown", "CoordinatorUnavailable", "IdentityConflict",
    "PostgresMissionCoordinator", "SCHEMA_VERSION", "StaleGeneration", "Submission",
]
