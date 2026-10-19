"""Integration tests against an actual isolated PostgreSQL server, never fake Blob.

CI installs embedded-postgres for a Unix-socket-only temporary server. No
Supabase/Vercel credential or unrelated project is used. Without the test-only
binary the suite skips; this is NOT a managed Preview integration test.
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from autonomous_agent.execution_checkpoint import ExecutionCheckpoint
from autonomous_agent.postgres_execution_audit import PostgresExecutionAuditStore
from autonomous_agent.postgres_execution_checkpoint import PostgresExecutionCheckpointStore
from autonomous_agent.postgres_coordinator import (
    CommitOutcomeUnknown,
    CoordinatorUnavailable,
    IdentityConflict,
    PostgresMissionCoordinator,
    StaleGeneration,
)
from autonomous_agent.vercel_state import SharedStateCoordinationUnavailable, require_atomic_state_coordinator


@pytest.fixture(scope="module")
def postgres_dsn(tmp_path_factory) -> str:
    server_package = pytest.importorskip("embedded_postgres", reason="install test-only embedded-postgres for real PG tests")
    location = tmp_path_factory.mktemp("real-coordinator") / "pgdata"
    location.mkdir()
    server = server_package.PostgresServer(location, cleanup_mode="stop")
    with psycopg.connect(server.get_uri(), autocommit=True) as connection:
        migration_dir = Path(__file__).resolve().parents[1] / "migrations"
        for migration in sorted(migration_dir.glob("*.sql")):
            connection.execute(migration.read_text(encoding="utf-8"))
    yield server.get_uri()


@pytest.fixture(scope="module")
def runtime_dsn(postgres_dsn):
    """Separate local role with the proposed Preview runtime privileges only."""
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    role = "scout_runtime_" + uuid.uuid4().hex[:12]
    with psycopg.connect(postgres_dsn) as admin:
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
        grants = (
            "USAGE ON SCHEMA scout_state",
            "SELECT ON scout_state.schema_version",
            "SELECT, INSERT ON scout_state.submissions",
            "SELECT, INSERT, UPDATE ON scout_state.missions, scout_state.work_items, "
            "scout_state.approvals, scout_state.audit_heads",
            "SELECT, INSERT ON scout_state.audit_events, scout_state.memories",
            "SELECT, INSERT ON scout_state.run_journal",
            "SELECT, INSERT, UPDATE ON scout_state.execution_checkpoints",
        )
        for permissions in grants:
            admin.execute(sql.SQL("GRANT " + permissions + " TO {}").format(sql.Identifier(role)))
    try:
        yield make_conninfo(postgres_dsn, user=role)
    finally:
        with psycopg.connect(postgres_dsn) as admin:
            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.fixture
def coordinator(runtime_dsn):
    instance = PostgresMissionCoordinator(runtime_dsn, "preview/" + uuid.uuid4().hex)
    instance.check_ready()
    return instance


def submit(coordinator, key: str = "request-1", *, approval: bool = False, output: bool = False):
    return coordinator.submit(
        key, {"task": "Read a harmless temporary README",
              "authorization_digest": "a" * 64, "criteria_digest": "b" * 64},
        {"task": "Read a harmless temporary README"}, {"task": "read-only"},
        requires_approval=approval, requires_output=output,
    )


def query(dsn, sql, parameters=()):
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, parameters).fetchall()


def test_schema_probe_is_read_only_and_hosted_gate_stays_closed(postgres_dsn, monkeypatch):
    store = PostgresMissionCoordinator(postgres_dsn, "preview/check")
    store.check_ready()
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("SCOUT_STATE_BACKEND", "postgres")
    monkeypatch.setenv("SCOUT_STATE_DATABASE_URL", postgres_dsn)
    with pytest.raises(SharedStateCoordinationUnavailable):
        require_atomic_state_coordinator()
    assert query(postgres_dsn, "SELECT version FROM scout_state.schema_version") == [(3,)]


def test_runtime_role_has_no_ddl_delete_or_audit_rewrite_privilege(runtime_dsn):
    with psycopg.connect(runtime_dsn) as conn:
        assert conn.execute(
            "SELECT has_table_privilege(current_user, 'scout_state.audit_events', 'SELECT, INSERT')"
        ).fetchone() == (True,)
        assert conn.execute(
            "SELECT has_table_privilege(current_user, 'scout_state.audit_events', 'UPDATE')"
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT has_table_privilege(current_user, 'scout_state.work_items', 'DELETE')"
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT has_table_privilege(current_user, 'scout_state.run_journal', 'SELECT, INSERT')"
        ).fetchone() == (True,)
        assert conn.execute(
            "SELECT has_table_privilege(current_user, 'scout_state.run_journal', 'UPDATE, DELETE')"
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT has_schema_privilege(current_user, 'scout_state', 'CREATE')"
        ).fetchone() == (False,)


def test_missing_schema_never_migrates_itself(postgres_dsn):
    # Create an empty database only inside the isolated local test server.
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    name = "scout_empty_" + uuid.uuid4().hex[:10]
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    empty_dsn = make_conninfo(postgres_dsn, dbname=name)
    try:
        with pytest.raises(CoordinatorUnavailable):
            PostgresMissionCoordinator(empty_dsn, "preview/empty").check_ready()
        with psycopg.connect(empty_dsn) as conn:
            assert conn.execute("SELECT to_regclass('scout_state.schema_version')").fetchone() == (None,)
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
    with pytest.raises(ValueError):
        PostgresMissionCoordinator("", "preview/no-connection")
    with pytest.raises(ValueError):
        PostgresMissionCoordinator(postgres_dsn, "../invalid namespace")
    with pytest.raises(CoordinatorUnavailable):
        PostgresMissionCoordinator("host=/nonexistent-path dbname=postgres", "preview/test").check_ready()


def test_duplicate_submission_is_one_atomic_mission_and_queue_row(coordinator, postgres_dsn):
    barrier = threading.Barrier(8)
    def request(_):
        barrier.wait()
        return submit(coordinator)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(request, range(8)))
    assert len({(r.mission_id, r.task_id, r.execution_id) for r in outcomes}) == 1
    assert sum(not r.duplicated for r in outcomes) == 1
    assert len(query(postgres_dsn, "SELECT task_id FROM scout_state.work_items WHERE namespace = %s",
                     (coordinator.namespace,))) == 1
    with pytest.raises(IdentityConflict):
        coordinator.submit("request-1", {"task": "different", "authorization_digest": "a" * 64,
                                         "criteria_digest": "b" * 64}, {}, {})
    with pytest.raises(IdentityConflict):
        submit(coordinator, approval=True)  # same key must not change approval authority
    with pytest.raises(ValueError, match="authorization_digest"):
        coordinator.submit("unsafe-request", {"task": "unfenced"}, {}, {})
    assert coordinator.mission(outcomes[0].mission_id)["state"] == "pending"


def test_competing_workers_claim_each_item_once_with_fenced_generation(coordinator, postgres_dsn):
    created = [submit(coordinator, f"request-{i}") for i in range(16)]
    barrier = threading.Barrier(16)
    def claim(index):
        barrier.wait()
        return coordinator.claim_next(f"worker-{index}")
    with ThreadPoolExecutor(max_workers=16) as pool:
        claims = list(pool.map(claim, range(16)))
    assert all(claims)
    assert {c.task_id for c in claims} == {r.task_id for r in created}
    assert all(c.generation == 1 for c in claims)
    assert coordinator.claim_next("worker-extra") is None
    for claimed in claims:
        coordinator.heartbeat(claimed)
        coordinator.finish(claimed, "failed")
    assert all(coordinator.mission(r.mission_id)["state"] == "failed" for r in created)
    assert query(postgres_dsn, "SELECT count(*) FROM scout_state.audit_events WHERE namespace = %s",
                 (coordinator.namespace,)) == [(16,)]


def test_crash_rollback_releases_row_lock_without_unconditional_delete(coordinator, postgres_dsn):
    work = submit(coordinator)
    conn = psycopg.connect(postgres_dsn)
    try:
        row = conn.execute(
            "SELECT task_id FROM scout_state.work_items WHERE namespace = %s AND task_id = %s FOR UPDATE",
            (coordinator.namespace, work.task_id),
        ).fetchone()
        assert row == (work.task_id,)
        conn.execute(
            "UPDATE scout_state.work_items SET state = 'running' WHERE namespace = %s AND task_id = %s",
            (coordinator.namespace, work.task_id),
        )
        assert coordinator.claim_next("other-worker") is None  # SKIP LOCKED, no duplicate claim
    finally:
        conn.close()  # simulate process death: PostgreSQL rolls back and unlocks
    claim = coordinator.claim_next("survivor")
    assert claim and claim.task_id == work.task_id and claim.generation == 1
    coordinator.finish(claim, "failed")


def test_reaper_fences_stale_worker_but_never_requeues_automatically(coordinator, postgres_dsn):
    work = submit(coordinator)
    old = coordinator.claim_next("old-worker")
    assert old is not None
    with psycopg.connect(postgres_dsn) as conn:
        conn.execute(
            """UPDATE scout_state.work_items SET heartbeat_at = clock_timestamp() - interval '2 hours'
               WHERE namespace = %s AND task_id = %s""", (coordinator.namespace, work.task_id),
        )
    assert coordinator.recover_stale(older_than_seconds=60) == (work.task_id,)
    assert coordinator.recover_stale(older_than_seconds=60) == ()
    with pytest.raises(StaleGeneration):
        coordinator.heartbeat(old)
    with pytest.raises(StaleGeneration):
        coordinator.finish(old, "verified")
    assert coordinator.claim_next("replacement") is None  # requires human reconciliation
    assert coordinator.mission(work.mission_id)["state"] == "recovery_required"
    assert coordinator.mission(work.mission_id)["generation"] == 2
    assert [e.kind for e in coordinator.timeline(work.mission_id)] == ["recovery_required"]


def test_approval_requires_pending_decision_token_and_atomic_one_time_activation(coordinator, postgres_dsn):
    work = submit(coordinator, approval=True)
    assert coordinator.claim_next("pre-approval") is None
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    coordinator.propose_approval("action-1", work.mission_id, expires, {"risk": "medium"})
    assert not coordinator.activate_approved("action-1", "a" * 40)
    coordinator.decide_approval("action-1", "approved", approval_token="a" * 40)
    assert not coordinator.activate_approved("action-1", "different" * 5)
    barrier = threading.Barrier(6)
    def consume(_):
        barrier.wait()
        return coordinator.activate_approved("action-1", "a" * 40)
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(consume, range(6))) == 1
    assert not coordinator.activate_approved("action-1", "a" * 40)
    assert query(postgres_dsn, "SELECT count(*) FROM scout_state.work_items WHERE namespace = %s",
                 (coordinator.namespace,)) == [(1,)]
    claim = coordinator.claim_next("approved-worker")
    assert claim and claim.mission_id == work.mission_id
    coordinator.finish(claim, "failed")
    assert [e.kind for e in coordinator.timeline(work.mission_id)] == [
        "approval_decision", "approval_consumed", "mission_state",
    ]


def test_expired_or_rejected_approval_cannot_activate(coordinator):
    expired = submit(coordinator, "expired", approval=True)
    coordinator.propose_approval("expired-action", expired.mission_id,
                                 datetime.now(timezone.utc) - timedelta(minutes=1), {})
    with pytest.raises(StaleGeneration):
        coordinator.decide_approval("expired-action", "approved", approval_token="t" * 40)
    assert not coordinator.activate_approved("expired-action", "t" * 40)
    rejected = submit(coordinator, "rejected", approval=True)
    coordinator.propose_approval("rejected-action", rejected.mission_id,
                                 datetime.now(timezone.utc) + timedelta(hours=1), {})
    coordinator.decide_approval("rejected-action", "rejected")
    assert not coordinator.activate_approved("rejected-action", "t" * 40)
    assert coordinator.claim_next("unsanctioned-worker") is None


def test_checkpoints_are_claim_fenced_attempt_scoped_and_monotonic(coordinator):
    from dataclasses import replace

    submit(coordinator)
    claim = coordinator.claim_next("checkpoint-worker")
    assert claim is not None
    assert coordinator.load_checkpoint(claim) is None

    saved = coordinator.save_checkpoint(
        claim,
        task_digest="a" * 64,
        plan_digest="b" * 64,
        authorization_digest="c" * 64,
        state="running",
        completed_step_ids=("step-1",),
        total_attempts=1,
    )
    assert saved["state"] == "running"
    assert saved["completed_step_ids"] == ("step-1",)
    assert coordinator.load_checkpoint(claim) == saved

    with pytest.raises(IdentityConflict, match="checkpoint identity or progress"):
        coordinator.save_checkpoint(
            claim,
            task_digest="a" * 64,
            plan_digest="b" * 64,
            authorization_digest="c" * 64,
            state="running",
            completed_step_ids=(),
            total_attempts=1,
        )
    with pytest.raises(IdentityConflict, match="checkpoint identity or progress"):
        coordinator.save_checkpoint(
            claim,
            task_digest="a" * 64,
            plan_digest="d" * 64,
            authorization_digest="c" * 64,
            state="running",
            completed_step_ids=("step-1",),
            total_attempts=1,
        )

    stale = replace(claim, generation=claim.generation + 1)
    with pytest.raises(StaleGeneration):
        coordinator.load_checkpoint(stale)
    with pytest.raises(StaleGeneration):
        coordinator.save_checkpoint(
            stale,
            task_digest="a" * 64,
            plan_digest="b" * 64,
            authorization_digest="c" * 64,
            state="running",
            completed_step_ids=("step-1", "step-2"),
            total_attempts=2,
        )
    assert coordinator.load_checkpoint(claim) == saved


def test_postgres_checkpoint_store_matches_execution_engine_contract(coordinator):
    submit(coordinator, "checkpoint-adapter")
    claim = coordinator.claim_next("checkpoint-adapter-worker")
    assert claim is not None
    store = PostgresExecutionCheckpointStore(coordinator, claim)
    assert store.load() is None

    saved = store.save(
        execution_id=claim.execution_id,
        task_digest="a" * 64,
        plan_digest="b" * 64,
        authorization_digest="c" * 64,
        state="running",
        completed_step_ids=("step-1",),
        total_attempts=1,
    )
    assert isinstance(saved, ExecutionCheckpoint)
    assert saved.execution_id == claim.execution_id
    assert saved.completed_step_ids == ("step-1",)
    assert store.load() == saved

    with pytest.raises(ValueError, match="execution identity"):
        store.save(
            execution_id="exec-not-the-active-claim",
            task_digest="a" * 64,
            plan_digest="b" * 64,
            authorization_digest="c" * 64,
            state="running",
            completed_step_ids=("step-1",),
            total_attempts=1,
        )

def test_postgres_execution_audit_store_is_hash_chained_and_claim_fenced(coordinator):
    from dataclasses import replace

    submit(coordinator, "audit-adapter")
    claim = coordinator.claim_next("audit-adapter-worker")
    assert claim is not None
    audit = PostgresExecutionAuditStore(coordinator, claim)
    assert audit.verify()

    audit.append({
        "execution_id": claim.execution_id,
        "timestamp": "now",
        "state": "running",
        "event": "execution_started",
        "task_digest": "a" * 64,
    })
    audit.append({
        "execution_id": claim.execution_id,
        "timestamp": "later",
        "state": "running",
        "event": "tool_result",
        "tool": "filesystem.read",
        "result": "success",
        "verification": "verified",
        "step_id": "step-1",
    })
    assert audit.verify()
    records = audit.events(claim.execution_id)
    assert [item["event"] for item in records] == ["execution_started", "tool_result"]
    assert records[1]["verification"] == "verified"
    assert records[1]["audit_sequence"] == "2"

    stale_claim = replace(claim, generation=claim.generation + 1)
    stale_audit = PostgresExecutionAuditStore(coordinator, stale_claim)
    with pytest.raises(StaleGeneration):
        stale_audit.append({
            "execution_id": claim.execution_id,
            "timestamp": "stale",
            "state": "running",
            "event": "tool_result",
            "result": "success",
        })
    assert len(audit.events(claim.execution_id)) == 2


def test_postgres_mission_runner_executes_read_only_flow_end_to_end(coordinator, tmp_path, monkeypatch):
    from autonomous_agent.postgres_mission_runner import PostgresMissionRunner

    # Make the safety boundary deterministic even on non-CI developer hosts.
    for name in ("VERCEL", "VERCEL_ENV", "VERCEL_URL", "BLOB_READ_WRITE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    runner = PostgresMissionRunner(
        coordinator,
        root=tmp_path,
        worker_id="postgres-e2e-worker",
    )
    facts = (
        "The temporary workspace contains a synthetic README file.",
        "The README describes a bounded local acceptance canary.",
    )
    (tmp_path / "README.md").write_text("# Fixture\n" + "\n".join(facts) + "\n", encoding="utf-8")
    task = "Read the temporary README and summarize two facts."
    submitted = runner.submit(task, idempotency_key="read-only-e2e-1")
    duplicate = runner.submit(task, idempotency_key="read-only-e2e-1")
    assert duplicate.duplicated is True
    assert duplicate.mission_id == submitted.mission_id

    result = runner.run_once()
    assert result is not None
    assert result.state.value == "verified", result.reason
    mission = coordinator.mission(submitted.mission_id)
    assert mission is not None
    assert mission["state"] == "verified"
    assert result.output
    assert all(fact in result.output for fact in facts)
    assert mission["payload"]["output"] == result.output
    memories = coordinator.memory(submitted.mission_id)
    assert len(memories) == 1
    assert memories[0]["outcome"] == "verified"
    assert memories[0]["payload"]["summary"] == result.output
    assert coordinator.run_journal(submitted.mission_id)[0]["state"] == "verified"
    assert coordinator.run_journal(submitted.mission_id)[0]["task"] == task
    events = coordinator.timeline(submitted.mission_id, limit=100)
    kinds = [event.kind for event in events]
    assert "execution_started" in kinds
    assert "tool_result" in kinds
    assert "output_validated" in kinds
    assert "checkpoint_verified" in kinds
    assert "execution_result" in kinds
    assert kinds[-1] == "mission_state"
    validation = next(event for event in events if event.kind == "output_validated")
    assert validation.payload["output_sha256"] == hashlib.sha256(result.output.encode()).hexdigest()
    # The E2E run used DB-backed checkpoints/audit, not synthetic local state.
    assert not (tmp_path / "state" / "runtime_execution.jsonl").exists()
    assert not (tmp_path / "state" / "runtime_checkpoints").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8").startswith("# Fixture")
    assert runner.run_once() is None

def test_postgres_mission_runner_rejects_network_and_write_plans_before_submission(coordinator, tmp_path, monkeypatch):
    from autonomous_agent.postgres_mission_runner import PostgresMissionRunner

    for name in ("VERCEL", "VERCEL_ENV", "VERCEL_URL", "BLOB_READ_WRITE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    runner = PostgresMissionRunner(coordinator, root=tmp_path, worker_id="reject-unsafe-worker")

    with pytest.raises(ValueError, match="autonomous local read-only"):
        runner.submit("search the public web for current release notes", idempotency_key="deny-network")
    with pytest.raises(ValueError, match="not executable|autonomous local read-only"):
        runner.submit("write file report.txt", idempotency_key="deny-write")

    assert runner.run_once() is None


def test_postgres_mission_runner_idempotency_key_cannot_change_task(coordinator, tmp_path, monkeypatch):
    from autonomous_agent.postgres_mission_runner import PostgresMissionRunner
    from autonomous_agent.postgres_coordinator import IdentityConflict

    for name in ("VERCEL", "VERCEL_ENV", "VERCEL_URL", "BLOB_READ_WRITE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    runner = PostgresMissionRunner(coordinator, root=tmp_path, worker_id="idempotency-worker")
    (tmp_path / "a.txt").write_text("A harmless fixture.\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("Another harmless fixture.\n", encoding="utf-8")
    task_a = "Read file a.txt in the isolated workspace. Do not change any file. Do not execute shell commands. Do not use network, browser, GitHub, email, calendar, applications, or computer control."
    task_b = "Read file b.txt in the isolated workspace. Do not change any file. Do not execute shell commands. Do not use network, browser, GitHub, email, calendar, applications, or computer control."
    first = runner.submit(task_a, idempotency_key="same-key-different-task")
    with pytest.raises(IdentityConflict):
        runner.submit(task_b, idempotency_key="same-key-different-task")
    assert runner.coordinator.mission(first.mission_id)["state"] == "pending"


def test_failed_local_read_never_creates_verified_memory(coordinator, tmp_path, monkeypatch):
    from autonomous_agent.postgres_mission_runner import PostgresMissionRunner

    for name in ("VERCEL", "VERCEL_ENV", "VERCEL_URL", "BLOB_READ_WRITE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    runner = PostgresMissionRunner(coordinator, root=tmp_path, worker_id="failed-read-worker")
    task = "Read file missing.txt in the isolated workspace. Do not change any file. Do not execute shell commands. Do not use network, browser, GitHub, email, calendar, applications, or computer control."
    submitted = runner.submit(task, idempotency_key="missing-file-read")

    result = runner.run_once()
    assert result is not None
    assert result.state.value == "failed", result.reason
    mission = coordinator.mission(submitted.mission_id)
    assert mission is not None and mission["state"] == "failed"
    assert coordinator.memory(submitted.mission_id) == ()
    assert coordinator.run_journal(submitted.mission_id)[0]["state"] == "failed"
    assert any(
        event.kind == "tool_result" and event.payload.get("result") == "failure"
        for event in coordinator.timeline(submitted.mission_id, limit=100)
    )


def test_postgres_mission_runner_refuses_hosted_runtime_without_mutating_state(coordinator, tmp_path, monkeypatch):
    from autonomous_agent.postgres_mission_runner import PostgresMissionRunner

    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    with pytest.raises(SharedStateCoordinationUnavailable):
        PostgresMissionRunner(coordinator, root=tmp_path, worker_id="hosted-gate-worker")
    assert coordinator.claim_next("hosted-gate-worker") is None

def test_run_journal_is_claim_fenced_idempotent_and_readable(coordinator):
    from dataclasses import replace

    work = submit(coordinator, "journal-record")
    claim = coordinator.claim_next("journal-worker")
    assert claim is not None
    record = {
        "execution_id": claim.execution_id,
        "task": "Read a harmless temporary README",
        "state": "recovery_required",
        "reason": "worker stopped; explicit reconciliation needed",
        "attempts": 2,
        "result_count": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    coordinator.record_run(claim, record)
    coordinator.record_run(claim, record)
    assert coordinator.run_journal(work.mission_id) == (record,)

    with pytest.raises(IdentityConflict, match="run journal identity"):
        coordinator.record_run(claim, dict(record, reason="different content"))
    with pytest.raises(ValueError, match="only recovery-required"):
        coordinator.record_run(claim, dict(record, state="verified"))

    stale = replace(claim, generation=claim.generation + 1)
    with pytest.raises(StaleGeneration):
        coordinator.record_run(stale, dict(record, recorded_at=datetime.now(timezone.utc).isoformat()))
    assert coordinator.run_journal(work.mission_id) == (record,)


def test_output_memory_and_terminal_audit_are_atomic_on_real_postgres(coordinator, postgres_dsn):
    work = submit(coordinator, output=True)
    claim = coordinator.claim_next("reader")
    assert claim
    with pytest.raises(ValueError, match="validated"):
        coordinator.finish(claim, "verified")  # mere file read is not a two-fact summary
    with pytest.raises(ValueError, match="evidence"):
        coordinator.finish(claim, "verified", validated_output="fact one and fact two",
                           validation_event_id="missing")
    output = "fact one and fact two"
    digest = hashlib.sha256(output.encode()).hexdigest()
    run_record = {
        "execution_id": claim.execution_id,
        "task": "Read a harmless temporary README",
        "state": "verified",
        "reason": "validated output was produced",
        "attempts": 1,
        "result_count": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    event = coordinator.append_event(work.mission_id, "validated-1", "output_validated",
                                     work.execution_id, {"output_sha256": digest})
    assert event.sequence == 1
    with pytest.raises(ValueError, match="does not match terminal result"):
        coordinator.finish(
            claim, "verified", validated_output=output, validation_event_id="validated-1",
            memory={"summary": output}, run_record=dict(run_record, state="failed"),
        )
    assert coordinator.mission(work.mission_id)["state"] == "running"
    assert coordinator.memory(work.mission_id) == ()
    assert coordinator.run_journal(work.mission_id) == ()
    with pytest.raises(ValueError, match="JSON serializable"):
        coordinator.finish(claim, "verified", validated_output=output,
                           validation_event_id="validated-1", memory={"bad": {1, 2}}, run_record=run_record)
    assert coordinator.mission(work.mission_id)["state"] == "running"
    assert coordinator.memory(work.mission_id) == ()
    assert coordinator.run_journal(work.mission_id) == ()
    assert len(coordinator.timeline(work.mission_id)) == 1
    coordinator.finish(claim, "verified", validated_output=output,
                       validation_event_id="validated-1", memory={"summary": output}, run_record=run_record)
    assert coordinator.mission(work.mission_id)["payload"]["output"] == output
    memory = coordinator.memory(work.mission_id)
    assert len(memory) == 1 and memory[0]["outcome"] == "verified"
    assert coordinator.run_journal(work.mission_id) == (run_record,)
    assert [e.kind for e in coordinator.timeline(work.mission_id)] == ["output_validated", "mission_state"]
    with pytest.raises(StaleGeneration):
        coordinator.finish(claim, "verified", validated_output=output, validation_event_id="validated-1")
    assert len(coordinator.memory(work.mission_id)) == 1


def test_verified_read_requires_evidence_and_memory_is_attempt_scoped(coordinator):
    work = submit(coordinator)
    claim = coordinator.claim_next("read-worker")
    assert claim is not None
    with pytest.raises(ValueError, match="validated result"):
        coordinator.finish(claim, "verified")
    coordinator.append_event(work.mission_id, "read-result", "tool_result", work.execution_id,
                             {"result": "success", "verification": "verified"})
    coordinator.finish(claim, "verified", validation_event_id="read-result",
                       memory={"summary": "synthetic verified read"})
    assert coordinator.mission(work.mission_id)["state"] == "verified"
    assert coordinator.memory(work.mission_id)[0]["source_event_id"] == "read-result"
    assert [event.kind for event in coordinator.timeline(work.mission_id)] == ["tool_result", "mission_state"]


def test_audit_event_idempotency_chain_and_namespace_isolation(coordinator, postgres_dsn):
    first = submit(coordinator)
    event = coordinator.append_event(first.mission_id, "event-one", "tool_result", first.execution_id,
                                     {"verification": "verified"})
    duplicate = coordinator.append_event(first.mission_id, "event-one", "tool_result", first.execution_id,
                                         {"verification": "verified"})
    assert event == duplicate
    with pytest.raises(IdentityConflict):
        coordinator.append_event(first.mission_id, "event-one", "tool_result", first.execution_id, {"x": 1})
    isolated = PostgresMissionCoordinator(postgres_dsn, "preview/" + uuid.uuid4().hex)
    other = submit(isolated)
    assert other.mission_id != first.mission_id
    assert isolated.timeline(first.mission_id) == ()
    assert isolated.memory(first.mission_id) == ()
    assert isolated.claim_next("worker") is not None
    with psycopg.connect(postgres_dsn) as conn:
        conn.execute("UPDATE scout_state.audit_events SET payload = '{\"tampered\":true}'::jsonb "
                     "WHERE namespace = %s AND event_id = 'event-one'", (coordinator.namespace,))
    with pytest.raises(CoordinatorUnavailable, match="audit hash"):
        coordinator.timeline(first.mission_id)


def test_lost_commit_ack_reconciles_by_stable_identity_without_replay(coordinator, postgres_dsn):
    class UncertainOnce(PostgresMissionCoordinator):
        remaining = 1
        def _commit(self, connection):
            connection.commit()  # actual PostgreSQL commit happened
            if self.remaining:
                self.remaining -= 1
                raise psycopg.OperationalError("simulated lost commit acknowledgement")
    uncertain = UncertainOnce(postgres_dsn, coordinator.namespace)
    with pytest.raises(CommitOutcomeUnknown):
        submit(uncertain, "commit-unknown")
    work = submit(coordinator, "commit-unknown")
    assert work.duplicated
    assert query(postgres_dsn, "SELECT count(*) FROM scout_state.work_items WHERE namespace = %s",
                 (coordinator.namespace,)) == [(1,)]
    claim = coordinator.claim_next("safe-worker")
    assert claim is not None
    uncertain.remaining = 1
    with pytest.raises(CommitOutcomeUnknown):
        uncertain.append_event(work.mission_id, "idempotent-event", "tool_result",
                               work.execution_id, {"verification": "verified"})
    assert coordinator.append_event(work.mission_id, "idempotent-event", "tool_result",
                                    work.execution_id, {"verification": "verified"}).sequence == 1
    coordinator.finish(claim, "failed")
    assert len(coordinator.timeline(work.mission_id)) == 2


def test_concurrent_audit_appends_have_one_verifiable_order(coordinator, postgres_dsn):
    work = submit(coordinator)
    barrier = threading.Barrier(12)
    def append(index):
        barrier.wait()
        return coordinator.append_event(work.mission_id, f"audit-{index}", "tool_result",
                                        work.execution_id, {"result": "success", "verification": "verified"})
    with ThreadPoolExecutor(max_workers=12) as pool:
        events = list(pool.map(append, range(12)))
    assert {entry.sequence for entry in events} == set(range(1, 13))
    assert [entry.sequence for entry in coordinator.timeline(work.mission_id)] == list(range(1, 13))
    assert len(coordinator.timeline(work.mission_id, limit=5)) == 5


def test_lost_queue_claim_ack_never_grants_a_second_worker(coordinator, postgres_dsn):
    work = submit(coordinator)
    class UncertainClaim(PostgresMissionCoordinator):
        def _commit(self, connection):
            connection.commit()
            raise psycopg.OperationalError("simulated claim response loss")
    with pytest.raises(CommitOutcomeUnknown):
        UncertainClaim(postgres_dsn, coordinator.namespace).claim_next("uncertain-worker")
    assert coordinator.claim_next("second-worker") is None
    assert coordinator.mission(work.mission_id)["state"] == "running"
    with psycopg.connect(postgres_dsn) as conn:
        conn.execute(
            """UPDATE scout_state.work_items SET heartbeat_at = clock_timestamp() - interval '2 hours'
               WHERE namespace = %s AND task_id = %s""", (coordinator.namespace, work.task_id),
        )
    assert coordinator.recover_stale(older_than_seconds=60) == (work.task_id,)
    assert coordinator.claim_next("second-worker") is None
    assert coordinator.mission(work.mission_id)["state"] == "recovery_required"


def test_database_disconnect_before_commit_rolls_back_no_local_fallback(coordinator, postgres_dsn):
    class DisconnectBeforeCommit(PostgresMissionCoordinator):
        def _commit(self, connection):
            with psycopg.connect(postgres_dsn, autocommit=True) as killer:
                assert killer.execute("SELECT pg_terminate_backend(%s)",
                                      (connection.info.backend_pid,)).fetchone() == (True,)
            connection.commit()  # lost session; server rolled back uncommitted inserts

    broken = DisconnectBeforeCommit(postgres_dsn, coordinator.namespace)
    with pytest.raises(CommitOutcomeUnknown):
        submit(broken, "disconnected")
    assert query(postgres_dsn, "SELECT count(*) FROM scout_state.submissions WHERE namespace = %s",
                 (coordinator.namespace,)) == [(0,)]
    assert query(postgres_dsn, "SELECT count(*) FROM scout_state.work_items WHERE namespace = %s",
                 (coordinator.namespace,)) == [(0,)]
    # An explicitly reconciled request can retry its stable ID; never replay a
    # tool automatically on the basis of a broken connection or local file.
    assert not submit(coordinator, "disconnected").duplicated


def test_uncertain_approval_commit_still_enqueues_once(coordinator, postgres_dsn):
    work = submit(coordinator, approval=True)
    coordinator.propose_approval("approval-uncertain", work.mission_id,
                                 datetime.now(timezone.utc) + timedelta(hours=1), {})
    coordinator.decide_approval("approval-uncertain", "approved", approval_token="q" * 40)

    class UncertainApproval(PostgresMissionCoordinator):
        def _commit(self, connection):
            connection.commit()
            raise psycopg.OperationalError("simulated lost response after approval consumption")

    with pytest.raises(CommitOutcomeUnknown):
        UncertainApproval(postgres_dsn, coordinator.namespace).activate_approved("approval-uncertain", "q" * 40)
    assert not coordinator.activate_approved("approval-uncertain", "q" * 40)
    assert query(postgres_dsn, "SELECT count(*) FROM scout_state.work_items WHERE namespace = %s",
                 (coordinator.namespace,)) == [(1,)]
    assert coordinator.claim_next("worker") is not None
