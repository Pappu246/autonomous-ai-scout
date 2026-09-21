from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autonomous_agent.action_queue import PendingAction, load_queue
from autonomous_agent import approval_cli
from autonomous_agent.approved_coding_bridge import (
    execute_persisted_approved_run,
    mark_coding_run_approved,
    mark_coding_run_rejected,
    persist_ready_coding_run,
)
from autonomous_agent.approval_store import create_approval, reject_action
from autonomous_agent.coding_run_store import (
    MAX_RECORD_BYTES,
    CodingRunNotFound,
    CodingRunState,
    CodingRunStore,
    CodingRunStoreError,
)
from autonomous_agent.continuous_improvement import build_proposal
from autonomous_agent.github_worker import GitHubWorker, WorkerResult
from autonomous_agent.models import ProjectFinding
from autonomous_agent.patch_review import review_patch
from autonomous_agent.self_improvement import ImprovementRun, ImprovementStatus, PatchCandidate


SHA = "a" * 40
DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print("old")
+print("new")
"""
FILES = {"app.py": 'print("new")\n'}


def proposal():
    return build_proposal(
        "owner/repo",
        ProjectFinding(
            repository="owner/repo",
            severity="high",
            title="CI regression",
            detail="A regression was detected.",
            recommendation="Fix the regression.",
            confidence=0.9,
        ),
    )


def ready_run(item):
    return ImprovementRun(
        ImprovementStatus.READY_FOR_APPROVAL,
        item.fingerprint,
        (),
        PatchCandidate(DIFF, FILES, "fix regression", ("python -m pytest -q",)),
        review_patch(DIFF),
        "candidate is ready for approval",
        True,
    )


def pending_action(status: str = "pending"):
    return PendingAction(
        "action-bridge",
        "prepare approved source improvement",
        ("inspect", "test"),
        "high",
        "reviewed candidate",
        status,
        "2026-09-21T00:00:00+00:00",
    )


def write_queue(path: Path, action: PendingAction) -> None:
    path.write_text(json.dumps([asdict(action)]), encoding="utf-8")


def build_record(tmp_path: Path):
    store = CodingRunStore(tmp_path / "runs")
    queue = tmp_path / "queue.json"
    lifecycle = tmp_path / "lifecycle.jsonl"
    action = pending_action()
    write_queue(queue, action)
    item = proposal()
    record = persist_ready_coding_run(
        store,
        lifecycle_path=lifecycle,
        run_id="run-bridge",
        proposal=item,
        run=ready_run(item),
        action=action,
        repository="owner/repo",
        base_branch="main",
        head_branch="improvement/action-bridge",
        expected_head_sha=SHA,
    )
    return store, record, queue, lifecycle


def approve_record(tmp_path: Path, store: CodingRunStore, queue: Path, lifecycle: Path):
    audit = tmp_path / "audit.jsonl"
    approvals = tmp_path / "approvals"
    create_approval(queue, approvals, "action-bridge", audit_path=audit)
    action = next(item for item in load_queue(queue) if item.id == "action-bridge")
    mark_coding_run_approved(store, action=action, lifecycle_path=lifecycle)
    return approvals, audit


class Worker:
    def __init__(self, result: WorkerResult | None = None):
        self.requests = []
        self.result = result or WorkerResult("draft_pr_created", "draft PR created", pull_request="https://github.com/owner/repo/pull/9")

    def execute(self, request, *, now=None):
        self.requests.append(request)
        return self.result


def execute(tmp_path: Path, store, queue, lifecycle, approvals, audit, worker):
    return execute_persisted_approved_run(
        "action-bridge",
        store=store,
        queue_path=queue,
        approval_dir=approvals,
        audit_path=audit,
        lifecycle_path=lifecycle,
        claim_store=tmp_path / "claims",
        worker=worker,
        now=datetime.now(timezone.utc),
    )


def test_coding_run_persists_and_reloads_after_new_store_instance(tmp_path: Path):
    store, record, _, _ = build_record(tmp_path)

    restored = CodingRunStore(store.directory).load(record.run_id)

    assert restored.run_id == "run-bridge"
    assert restored.proposal == record.proposal
    assert restored.run.candidate == record.run.candidate
    assert restored.state is CodingRunState.READY_FOR_APPROVAL


def test_malformed_and_missing_records_fail_closed(tmp_path: Path):
    store = CodingRunStore(tmp_path / "runs")
    store.directory.mkdir()
    (store.directory / "run-bad.json").write_text("{not JSON", encoding="utf-8")

    with pytest.raises(CodingRunStoreError):
        store.load("run-bad")
    with pytest.raises(CodingRunNotFound):
        store.load("run-missing")


def test_approval_mismatch_invalidates_before_worker_execution(tmp_path: Path):
    store, _, queue, lifecycle = build_record(tmp_path)
    approvals, audit = approve_record(tmp_path, store, queue, lifecycle)
    write_queue(queue, PendingAction("action-bridge", "changed action", ("inspect",), "high", "changed", "approved", "2026-09-21T00:00:00+00:00"))
    worker = Worker()

    result = execute(tmp_path, store, queue, lifecycle, approvals, audit, worker)

    assert result.state == "blocked"
    assert worker.requests == []
    assert store.find_by_action("action-bridge").state is CodingRunState.INVALIDATED


@pytest.mark.parametrize("field,value", [("repository", "other/repo"), ("patch_digest", "0" * 64)])
def test_identity_or_patch_digest_tampering_never_reaches_worker(tmp_path: Path, field: str, value: str):
    store, record, _, _ = build_record(tmp_path)
    path = store.directory / f"{record.run_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CodingRunStoreError):
        store.load(record.run_id)


def test_execution_requires_explicit_approval(tmp_path: Path):
    store, _, queue, lifecycle = build_record(tmp_path)
    worker = Worker()

    result = execute_persisted_approved_run(
        "action-bridge",
        store=store,
        queue_path=queue,
        approval_dir=tmp_path / "approvals",
        audit_path=tmp_path / "audit.jsonl",
        lifecycle_path=lifecycle,
        claim_store=tmp_path / "claims",
        worker=worker,
    )

    assert result.state == "blocked"
    assert worker.requests == []
    assert store.find_by_action("action-bridge").state is CodingRunState.READY_FOR_APPROVAL


def test_rejected_action_cannot_execute(tmp_path: Path):
    store, _, queue, lifecycle = build_record(tmp_path)
    reject_action(queue, "action-bridge", audit_path=tmp_path / "audit.jsonl")
    mark_coding_run_rejected(store, action_id="action-bridge", lifecycle_path=lifecycle)
    worker = Worker()

    result = execute_persisted_approved_run(
        "action-bridge",
        store=store,
        queue_path=queue,
        approval_dir=tmp_path / "approvals",
        audit_path=tmp_path / "audit.jsonl",
        lifecycle_path=lifecycle,
        claim_store=tmp_path / "claims",
        worker=worker,
    )

    assert result.state == "blocked"
    assert worker.requests == []
    assert store.find_by_action("action-bridge").state is CodingRunState.REJECTED


def test_successful_approved_run_reaches_worker_and_is_one_shot(tmp_path: Path):
    store, _, queue, lifecycle = build_record(tmp_path)
    approvals, audit = approve_record(tmp_path, store, queue, lifecycle)
    worker = Worker()

    first = execute(tmp_path, store, queue, lifecycle, approvals, audit, worker)
    second = execute(tmp_path, store, queue, lifecycle, approvals, audit, worker)

    assert first.state == "draft_pr_created"
    assert second.state == "blocked"
    assert len(worker.requests) == 1
    assert store.find_by_action("action-bridge").state is CodingRunState.EXECUTED


def test_worker_failure_is_persisted_as_failed(tmp_path: Path):
    store, _, queue, lifecycle = build_record(tmp_path)
    approvals, audit = approve_record(tmp_path, store, queue, lifecycle)
    worker = Worker(WorkerResult("blocked", "simulated GitHub failure"))

    result = execute(tmp_path, store, queue, lifecycle, approvals, audit, worker)

    assert result.state == "blocked"
    record = store.find_by_action("action-bridge")
    assert record.state is CodingRunState.FAILED
    assert record.execution.worker_state == "blocked"


def test_stale_expected_head_is_blocked_by_existing_worker_boundary(tmp_path: Path):
    store, _, queue, lifecycle = build_record(tmp_path)
    approvals, audit = approve_record(tmp_path, store, queue, lifecycle)

    class Head:
        def head_sha(self, repository, branch):
            return "b" * 40

    class Prs:
        def find(self, repository, head_branch, base_branch, patch_digest):
            return None

    class Backend:
        def create_branch_at_sha(self, *args):
            raise AssertionError("stale HEAD must stop before mutation")

    worker = GitHubWorker(
        head_provider=Head(),
        existing_prs=Prs(),
        backend=Backend(),
        claim_store=tmp_path / "claims",
        pull_requests=object(),
        ci=object(),
    )

    result = execute(tmp_path, store, queue, lifecycle, approvals, audit, worker)

    assert result.state == "blocked"
    assert "HEAD mismatch" in result.reason
    assert store.find_by_action("action-bridge").state is CodingRunState.FAILED


def test_sensitive_content_is_rejected_without_a_persisted_record(tmp_path: Path):
    store = CodingRunStore(tmp_path / "runs")
    item = proposal()
    candidate = PatchCandidate(
        DIFF.replace('print("new")', 'API_KEY=supersecretvalue'),
        {"app.py": "API_KEY=supersecretvalue\n"},
        "unsafe secret",
    )
    run = ImprovementRun(ImprovementStatus.READY_FOR_APPROVAL, item.fingerprint, (), candidate, review_patch(candidate.unified_diff), "ready", True)

    with pytest.raises(CodingRunStoreError, match="sensitive"):
        store.create(
            run_id="run-secret",
            proposal=item,
            run=run,
            action=pending_action(),
            repository="owner/repo",
            base_branch="main",
            head_branch="improvement/action-bridge",
            expected_head_sha=SHA,
        )

    assert not (store.directory / "run-secret.json").exists()


def test_oversized_record_is_rejected(tmp_path: Path):
    store, record, _, _ = build_record(tmp_path)
    (store.directory / f"{record.run_id}.json").write_bytes(b"x" * (MAX_RECORD_BYTES + 1))

    with pytest.raises(CodingRunStoreError, match="size"):
        store.load(record.run_id)


def test_approval_cli_inspects_approves_and_executes_persisted_run(tmp_path: Path, monkeypatch, capsys):
    store, _, queue, lifecycle = build_record(tmp_path)
    audit = tmp_path / "audit.jsonl"
    approvals = tmp_path / "approvals"
    claims = tmp_path / "claims"
    common = [
        "--queue", str(queue),
        "--audit", str(audit),
        "--approvals", str(approvals),
        "--runs", str(store.directory),
        "--lifecycle", str(lifecycle),
        "--claims", str(claims),
    ]

    assert approval_cli.main([*common, "inspect", "action-bridge"]) == 0
    assert '"state": "READY_FOR_APPROVAL"' in capsys.readouterr().out
    assert approval_cli.main([*common, "approve", "action-bridge"]) == 0
    assert store.find_by_action("action-bridge").state is CodingRunState.APPROVED
    worker = Worker()
    monkeypatch.setattr(approval_cli, "build_github_worker_from_env", lambda **kwargs: worker)

    assert approval_cli.main([*common, "execute", "action-bridge"]) == 0
    assert "state=draft_pr_created" in capsys.readouterr().out
    assert len(worker.requests) == 1


def test_coding_run_persists_ci_observation(tmp_path: Path):
    store, _, queue, lifecycle = build_record(tmp_path)
    approvals, audit = approve_record(tmp_path, store, queue, lifecycle)
    store.begin_execution("run-bridge")
    store.mark_executed(
        "run-bridge",
        worker_state="draft_pr_created",
        reason="draft PR created",
        pull_request="https://github.com/owner/repo/pull/9",
        ci_status="queued",
    )
    updated = store.record_observation(
        "run-bridge",
        worker_state="ci_running",
        reason="CI is still running",
        ci_status="in_progress",
    )
    assert updated.state is CodingRunState.EXECUTED
    assert updated.execution.ci_status == "in_progress"
    assert updated.execution.pull_request.endswith("/9")
