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


SCHEMA_VERSION = 4
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
        """Read-only schema and minimum runtime-grant check; never provisions state."""
        with self._transaction() as conn:
            role_flags = conn.execute(
                """SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
                   FROM pg_roles WHERE rolname = current_user"""
            ).fetchone()
            if role_flags is None or any(role_flags):
                raise CoordinatorUnavailable("coordinator runtime role has unsafe elevated privileges")
            if not conn.execute(
                "SELECT has_schema_privilege(current_user, 'scout_state', 'USAGE')"
            ).fetchone()[0]:
                raise CoordinatorUnavailable("coordinator runtime role lacks schema USAGE")
            if conn.execute(
                "SELECT has_schema_privilege(current_user, 'scout_state', 'CREATE')"
            ).fetchone()[0]:
                raise CoordinatorUnavailable("coordinator runtime role has schema CREATE privilege")

            row = conn.execute("SELECT version FROM scout_state.schema_version WHERE singleton = 1").fetchone()
            if row is None or row[0] != SCHEMA_VERSION:
                raise CoordinatorUnavailable("unsupported coordinator schema version")

            tables = (
                "submissions", "missions", "work_items", "approvals", "audit_heads",
                "audit_events", "memories", "execution_checkpoints", "run_journal",
            )
            for name in tables:
                if conn.execute("SELECT to_regclass(%s)", ("scout_state." + name,)).fetchone()[0] is None:
                    raise CoordinatorUnavailable("coordinator schema is incomplete")

            # Validate the least-privilege contract before accepting work. A
            # successful schema-version read alone does not prove the runtime
            # role can safely claim, heartbeat, checkpoint, audit and finish.
            required = {
                "schema_version": ("SELECT",),
                "submissions": ("SELECT", "INSERT"),
                "missions": ("SELECT", "INSERT", "UPDATE"),
                "work_items": ("SELECT", "INSERT", "UPDATE"),
                "approvals": ("SELECT", "INSERT", "UPDATE"),
                "audit_heads": ("SELECT", "INSERT", "UPDATE"),
                "audit_events": ("SELECT", "INSERT"),
                "memories": ("SELECT", "INSERT"),
                "execution_checkpoints": ("SELECT", "INSERT", "UPDATE"),
                "run_journal": ("SELECT", "INSERT"),
            }
            required_pairs = [
                (table, privilege)
                for table, privileges in required.items()
                for privilege in privileges
            ]
            grants = conn.execute(
                """SELECT required.table_name, required.privilege,
                          has_table_privilege(
                              current_user, 'scout_state.' || required.table_name,
                              required.privilege
                          )
                   FROM unnest(%s::text[], %s::text[])
                        AS required(table_name, privilege)""",
                (
                    [table for table, _ in required_pairs],
                    [privilege for _, privilege in required_pairs],
                ),
            ).fetchall()
            if any(not allowed for _, _, allowed in grants):
                raise CoordinatorUnavailable("coordinator runtime role lacks required table privileges")

            # A role that can meet the minimum contract but can rewrite immutable
            # audit, delete work, or create triggers is still not safe for runtime.
            forbidden = [
                (table, privilege)
                for table in tables
                for privilege in ("DELETE", "TRUNCATE", "TRIGGER", "REFERENCES")
            ]
            forbidden.extend(
                (table, privilege)
                for table in ("schema_version", "submissions", "audit_events", "memories", "run_journal")
                for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
                if not (table in {"submissions", "audit_events", "memories", "run_journal"} and privilege == "INSERT")
            )
            forbidden_checks = conn.execute(
                """SELECT required.table_name, required.privilege,
                          has_table_privilege(
                              current_user, 'scout_state.' || required.table_name,
                              required.privilege
                          )
                   FROM unnest(%s::text[], %s::text[])
                        AS required(table_name, privilege)""",
                (
                    [table for table, _ in forbidden],
                    [privilege for _, privilege in forbidden],
                ),
            ).fetchall()
            if any(allowed for _, _, allowed in forbidden_checks):
                raise CoordinatorUnavailable("coordinator runtime role has unsafe table privileges")

    def submit(
        self, idempotency_key: str, request: Mapping[str, object],
        mission: Mapping[str, object], queue: Mapping[str, object],
        *, requires_output: bool = False, requires_approval: bool = False,
        approval_action_id: str | None = None,
        approval_expires_at: datetime | None = None,
        approval_payload: Mapping[str, object] | None = None,
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
        mission_data, mission_bytes = _json(mission, "mission")
        queue_data, queue_bytes = _json(queue, "queue item")
        proposal_supplied = any(value is not None for value in
                                (approval_action_id, approval_expires_at, approval_payload))
        if proposal_supplied and not requires_approval:
            raise ValueError("approval proposal requires an approval-gated submission")
        if proposal_supplied and not all(value is not None for value in
                                         (approval_action_id, approval_expires_at, approval_payload)):
            raise ValueError("atomic approval proposal needs an action ID, expiry and payload")
        approval_data = None
        approval_expiry = None
        approval_descriptor = None
        if proposal_supplied:
            approval_action_id = _id(approval_action_id, "approval action")
            approval_expiry = _aware(approval_expires_at, "expiry")
            approval_data, approval_bytes = _json(approval_payload, "approval proposal")
            approval_descriptor = [approval_action_id, approval_expiry.isoformat(),
                                   hashlib.sha256(approval_bytes).hexdigest()]
        # Bind the approval proposal as well as request, mission and queue data
        # to the caller-generated idempotency key.
        digest = hashlib.sha256(json.dumps(
            [hashlib.sha256(request_bytes).hexdigest(),
             hashlib.sha256(mission_bytes).hexdigest(),
             hashlib.sha256(queue_bytes).hexdigest(),
             bool(requires_output), bool(requires_approval), approval_descriptor],
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
                   (namespace, mission_id, task_id, execution_id, state, requires_output, payload,
                    approval_queue_payload)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (self.namespace, mission_id, task_id, execution_id,
                 "requires_approval" if requires_approval else "pending", bool(requires_output),
                 Jsonb(mission_data), Jsonb(queue_data) if requires_approval else None),
            )
            if proposal_supplied:
                conn.execute(
                    """INSERT INTO scout_state.approvals
                       (namespace, action_id, mission_id, state, expires_at, payload)
                       VALUES (%s, %s, %s, 'pending', %s, %s)""",
                    (self.namespace, approval_action_id, mission_id, approval_expiry, Jsonb(approval_data)),
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

    def append_claim_event(
        self, claim: Claim, event_id: str, kind: str, execution_id: str,
        payload: Mapping[str, object],
    ) -> AuditEvent:
        """Append execution evidence only while the associated worker claim is current."""
        execution = _id(execution_id, "execution ID")
        if execution != claim.execution_id and not execution.startswith(claim.execution_id + ":"):
            raise ValueError("execution evidence does not belong to the active claim")
        with self._transaction() as conn:
            self._assert_live_claim(conn, claim, lock=True)
            return self._append_event(conn, claim.mission_id, event_id, kind, execution, payload)

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

    @staticmethod
    def _run_record_values(claim: Claim, record: Mapping[str, object]) -> tuple[object, ...]:
        if not isinstance(record, Mapping):
            raise ValueError("run journal record must be an object")
        try:
            execution_id = record["execution_id"]
            task = record["task"]
            state = record["state"]
            reason = record["reason"]
            attempts = record["attempts"]
            result_count = record["result_count"]
            recorded_at_text = record["recorded_at"]
        except KeyError as exc:
            raise ValueError("run journal record has missing fields") from exc
        if execution_id != claim.execution_id:
            raise ValueError("run journal execution ID does not match the active claim")
        if not isinstance(task, str) or not task or len(task) > 4000:
            raise ValueError("run journal task is empty or exceeds the size limit")
        if not isinstance(state, str) or state not in {
            "running", "verified", "partial", "failed", "blocked", "recovery_required"
        }:
            raise ValueError("run journal state is invalid")
        if not isinstance(reason, str) or len(reason) > 4096:
            raise ValueError("run journal reason exceeds the size limit")
        for name, value in (("attempts", attempts), ("result_count", result_count)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"run journal {name} must be a non-negative integer")
        if not isinstance(recorded_at_text, str) or len(recorded_at_text) > 64:
            raise ValueError("run journal timestamp is invalid")
        try:
            recorded_at = datetime.fromisoformat(recorded_at_text)
        except ValueError as exc:
            raise ValueError("run journal timestamp is invalid") from exc
        _aware(recorded_at, "run journal recorded_at")
        return (claim.mission_id, claim.task_id, claim.execution_id, claim.generation,
                task, state, reason, attempts, result_count, recorded_at)

    def _insert_run_record(self, conn, claim: Claim, record: Mapping[str, object]) -> None:
        values = self._run_record_values(claim, record)
        inserted = conn.execute(
            """INSERT INTO scout_state.run_journal
               (namespace, mission_id, task_id, execution_id, claim_generation,
                task, state, reason, attempts, result_count, recorded_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (namespace, task_id, claim_generation) DO NOTHING
               RETURNING 1""",
            (self.namespace, *values),
        ).fetchone()
        if inserted is not None:
            return
        prior = conn.execute(
            """SELECT mission_id, task_id, execution_id, claim_generation,
                      task, state, reason, attempts, result_count, recorded_at
               FROM scout_state.run_journal
               WHERE namespace = %s AND task_id = %s AND claim_generation = %s""",
            (self.namespace, claim.task_id, claim.generation),
        ).fetchone()
        if prior != values:
            raise IdentityConflict("run journal identity was reused with different content")

    def record_run(self, claim: Claim, record: Mapping[str, object]) -> None:
        """Persist a recovery result while its worker claim remains current."""
        values = self._run_record_values(claim, record)
        if values[5] != "recovery_required":
            raise ValueError("only recovery-required results may be recorded outside terminal commit")
        with self._transaction() as conn:
            self._assert_live_claim(conn, claim, lock=True)
            self._insert_run_record(conn, claim, record)

    def run_journal(self, mission_id: str, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("run journal limit must be between 1 and 1000")
        mission = _id(mission_id, "mission ID")
        with self._transaction() as conn:
            rows = conn.execute(
                """SELECT execution_id, task, state, reason, attempts, result_count, recorded_at
                   FROM scout_state.run_journal
                   WHERE namespace = %s AND mission_id = %s
                   ORDER BY recorded_at, claim_generation LIMIT %s""",
                (self.namespace, mission, limit),
            ).fetchall()
        return tuple({
            "execution_id": row[0],
            "task": row[1],
            "state": row[2],
            "reason": row[3],
            "attempts": row[4],
            "result_count": row[5],
            "recorded_at": row[6].isoformat(),
        } for row in rows)


    def finish(self, claim: Claim, state: str, *, validated_output: str = "",
               validation_event_id: str | None = None,
               memory: Mapping[str, object] | None = None,
               run_record: Mapping[str, object] | None = None) -> None:
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
        if run_record is not None:
            values = self._run_record_values(claim, run_record)
            allowed_journal_states = {
                "verified": {"verified"},
                "partial": {"partial"},
                "failed": {"failed", "blocked"},
            }
            if values[5] not in allowed_journal_states[state]:
                raise ValueError("run journal state does not match terminal result")
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
            if run_record is not None:
                self._insert_run_record(conn, claim, run_record)
            # The terminal event, journal and memory commit with the state transition.

    @staticmethod
    def _checkpoint_document(row) -> dict[str, object]:
        return {
            "schema_version": row[0],
            "execution_id": row[1],
            "task_digest": row[2],
            "plan_digest": row[3],
            "authorization_digest": row[4],
            "state": row[5],
            "completed_step_ids": tuple(row[6]),
            "total_attempts": row[7],
            "updated_at": row[8].isoformat(),
        }

    def _assert_live_claim(self, conn, claim: Claim, *, lock: bool) -> None:
        suffix = " FOR UPDATE OF q, m" if lock else " FOR SHARE OF q, m"
        row = conn.execute(
            """SELECT q.execution_id, q.claim_generation, q.worker_id, q.state,
                      m.state, m.claim_generation
               FROM scout_state.work_items AS q
               JOIN scout_state.missions AS m
                 ON m.namespace = q.namespace AND m.mission_id = q.mission_id
                AND m.task_id = q.task_id AND m.execution_id = q.execution_id
               WHERE q.namespace = %s AND q.task_id = %s AND q.mission_id = %s""" + suffix,
            (self.namespace, claim.task_id, claim.mission_id),
        ).fetchone()
        if row != (claim.execution_id, claim.generation, claim.worker_id, "running",
                   "running", claim.generation):
            raise StaleGeneration("worker claim is no longer current")

    def save_checkpoint(
        self,
        claim: Claim,
        *,
        task_digest: str,
        plan_digest: str,
        authorization_digest: str,
        state: str,
        completed_step_ids: tuple[str, ...],
        total_attempts: int,
    ) -> dict[str, object]:
        """Persist execution progress under the current worker generation.

        Checkpoints are attempt-scoped. A stale worker cannot overwrite a newer
        generation; task/plan/authorization identity and completed-step progress
        cannot regress within one generation.
        """
        for name, value in (("task digest", task_digest), ("plan digest", plan_digest),
                            ("authorization digest", authorization_digest)):
            if not isinstance(value, str) or not _HEX_DIGEST.fullmatch(value):
                raise ValueError(f"{name} must be a SHA-256 digest")
        if state not in {"running", "verified", "partial", "failed", "blocked"}:
            raise ValueError("invalid execution checkpoint state")
        if isinstance(total_attempts, bool) or not isinstance(total_attempts, int) or total_attempts < 0:
            raise ValueError("checkpoint attempts must be a non-negative integer")
        if not isinstance(completed_step_ids, (tuple, list)) or len(completed_step_ids) > 256:
            raise ValueError("checkpoint completed steps must be a bounded list")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 128
               for item in completed_step_ids):
            raise ValueError("checkpoint step IDs must be non-empty bounded strings")
        completed = tuple(dict.fromkeys(completed_step_ids))
        steps_json = Jsonb(list(completed))
        with self._transaction() as conn:
            self._assert_live_claim(conn, claim, lock=True)
            row = conn.execute(
                """INSERT INTO scout_state.execution_checkpoints
                   (namespace, task_id, mission_id, execution_id, claim_generation,
                    schema_version, task_digest, plan_digest, authorization_digest,
                    state, completed_step_ids, total_attempts)
                   VALUES (%s, %s, %s, %s, %s, 2, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (namespace, task_id, execution_id, claim_generation)
                   DO UPDATE SET state = EXCLUDED.state,
                     completed_step_ids = EXCLUDED.completed_step_ids,
                     total_attempts = EXCLUDED.total_attempts,
                     updated_at = clock_timestamp()
                   WHERE execution_checkpoints.mission_id = EXCLUDED.mission_id
                     AND execution_checkpoints.task_digest = EXCLUDED.task_digest
                     AND execution_checkpoints.plan_digest = EXCLUDED.plan_digest
                     AND execution_checkpoints.authorization_digest = EXCLUDED.authorization_digest
                     AND execution_checkpoints.completed_step_ids <@ EXCLUDED.completed_step_ids
                     AND execution_checkpoints.total_attempts <= EXCLUDED.total_attempts
                     AND (execution_checkpoints.state = 'running' OR
                          (execution_checkpoints.state = EXCLUDED.state
                           AND execution_checkpoints.completed_step_ids = EXCLUDED.completed_step_ids
                           AND execution_checkpoints.total_attempts = EXCLUDED.total_attempts))
                   RETURNING schema_version, execution_id, task_digest, plan_digest,
                             authorization_digest, state, completed_step_ids, total_attempts, updated_at""",
                (self.namespace, claim.task_id, claim.mission_id, claim.execution_id,
                 claim.generation, task_digest, plan_digest, authorization_digest,
                 state, steps_json, total_attempts),
            ).fetchone()
            if row is None:
                raise IdentityConflict("checkpoint identity or progress conflicts with the existing attempt")
            return self._checkpoint_document(row)

    def load_checkpoint(self, claim: Claim) -> dict[str, object] | None:
        """Load only a checkpoint belonging to the currently active claim."""
        with self._transaction() as conn:
            self._assert_live_claim(conn, claim, lock=False)
            row = conn.execute(
                """SELECT schema_version, execution_id, task_digest, plan_digest,
                          authorization_digest, state, completed_step_ids, total_attempts, updated_at
                   FROM scout_state.execution_checkpoints
                   WHERE namespace = %s AND task_id = %s AND mission_id = %s
                     AND execution_id = %s AND claim_generation = %s""",
                (self.namespace, claim.task_id, claim.mission_id, claim.execution_id, claim.generation),
            ).fetchone()
            return None if row is None else self._checkpoint_document(row)

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
                   RETURNING task_id, execution_id, approval_queue_payload""",
                (self.namespace, mission_id),
            ).fetchone()
            if mission is None:
                raise StaleGeneration("approved mission is no longer actionable")
            queue_payload = mission[2]
            if not isinstance(queue_payload, dict):
                raise CoordinatorUnavailable("approved mission lost its original queue payload; reconcile before activation")
            conn.execute(
                """INSERT INTO scout_state.work_items
                   (namespace, task_id, mission_id, execution_id, state, payload)
                   VALUES (%s, %s, %s, %s, 'pending', %s)""",
                (self.namespace, mission[0], mission_id, mission[1], Jsonb(queue_payload)),
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
