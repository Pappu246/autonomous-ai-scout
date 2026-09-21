from __future__ import annotations

"""Cross-process approval-to-GitHub bridge for persisted coding runs."""

from datetime import datetime
from pathlib import Path

from .action_lifecycle import LifecycleState
from .action_queue import PendingAction, load_queue
from .approved_coding import build_approved_coding_request, execute_approved_coding_request
from .approved_executor import action_fingerprint, validate_approval
from .approval_store import load_approval
from .coding_run_store import CodingRunState, CodingRunStore, CodingRunStoreError, StoredCodingRun
from .github_worker import GitHubWorker, WorkerResult
from .lifecycle_integration import record_transition, require_state
from .lifecycle_ledger import action_state


def register_ready_coding_run(lifecycle_path: Path, record: StoredCodingRun) -> None:
    """Record the completed pre-approval gates backed by the reviewed run."""
    current = action_state(lifecycle_path, record.action_id) if lifecycle_path.exists() else None
    if current is LifecycleState.POLICY_CHECKED:
        return
    if current is not None:
        raise CodingRunStoreError("coding action lifecycle is not ready for approval")
    transitions = (
        (LifecycleState.PROPOSED, LifecycleState.VALIDATED),
        (LifecycleState.VALIDATED, LifecycleState.TESTED),
        (LifecycleState.TESTED, LifecycleState.SECURED),
        (LifecycleState.SECURED, LifecycleState.POLICY_CHECKED),
    )
    for source, target in transitions:
        persisted, reason = record_transition(lifecycle_path, record.action_id, source, target)
        if not persisted:
            raise CodingRunStoreError(reason)


def persist_ready_coding_run(
    store: CodingRunStore,
    *,
    lifecycle_path: Path,
    run_id: str,
    proposal,
    run,
    action: PendingAction,
    repository: str,
    base_branch: str,
    head_branch: str,
    expected_head_sha: str,
    now: datetime | None = None,
) -> StoredCodingRun:
    record = store.create(
        run_id=run_id,
        proposal=proposal,
        run=run,
        action=action,
        repository=repository,
        base_branch=base_branch,
        head_branch=head_branch,
        expected_head_sha=expected_head_sha,
        now=now,
    )
    register_ready_coding_run(lifecycle_path, record)
    return record


def mark_coding_run_approved(
    store: CodingRunStore,
    *,
    action: PendingAction,
    lifecycle_path: Path,
    now: datetime | None = None,
) -> StoredCodingRun:
    record = store.find_by_action(action.id)
    trusted, reason = require_state(lifecycle_path, action.id, LifecycleState.POLICY_CHECKED)
    if not trusted:
        raise CodingRunStoreError(reason)
    updated = store.mark_approved(record.run_id, action, now=now)
    persisted, reason = record_transition(lifecycle_path, action.id, LifecycleState.POLICY_CHECKED, LifecycleState.APPROVED)
    if not persisted:
        raise CodingRunStoreError(reason)
    return updated


def mark_coding_run_rejected(
    store: CodingRunStore,
    *,
    action_id: str,
    lifecycle_path: Path,
    now: datetime | None = None,
) -> StoredCodingRun:
    record = store.find_by_action(action_id)
    updated = store.mark_rejected(record.run_id, now=now)
    state = action_state(lifecycle_path, action_id)
    if state in {LifecycleState.POLICY_CHECKED, LifecycleState.APPROVED}:
        persisted, reason = record_transition(lifecycle_path, action_id, state, LifecycleState.BLOCKED)
        if not persisted:
            raise CodingRunStoreError(reason)
    elif state is not LifecycleState.BLOCKED:
        raise CodingRunStoreError("coding action lifecycle is not rejectable")
    return updated


def _action_from_queue(queue_path: Path, action_id: str) -> PendingAction:
    matches = [item for item in load_queue(queue_path) if item.id == action_id]
    if len(matches) != 1:
        raise CodingRunStoreError("approved action is missing or ambiguous")
    return matches[0]


def _block_lifecycle(lifecycle_path: Path, action_id: str) -> None:
    state = action_state(lifecycle_path, action_id)
    if state in {LifecycleState.POLICY_CHECKED, LifecycleState.APPROVED, LifecycleState.CLAIMED}:
        record_transition(lifecycle_path, action_id, state, LifecycleState.BLOCKED)


def _invalidate(store: CodingRunStore, record: StoredCodingRun, lifecycle_path: Path, reason: str, now: datetime | None) -> WorkerResult:
    try:
        store.invalidate(record.run_id, reason, now=now)
        _block_lifecycle(lifecycle_path, record.action_id)
    except CodingRunStoreError:
        pass
    return WorkerResult("blocked", reason)


def execute_persisted_approved_run(
    action_id: str,
    *,
    store: CodingRunStore,
    queue_path: Path,
    approval_dir: Path,
    audit_path: Path,
    lifecycle_path: Path,
    claim_store: Path,
    worker: GitHubWorker,
    now: datetime | None = None,
) -> WorkerResult:
    """Execute exactly one stored run through the existing approved-coding boundary.

    All checks happen before the worker receives a request. The worker then repeats
    the approval, digest, target-HEAD, duplicate-PR, and single-use-claim checks
    immediately before its GitHub mutation boundary.
    """
    record = store.find_by_action(action_id)
    if record.state is not CodingRunState.APPROVED:
        return WorkerResult("blocked", f"coding run state is {record.state.value}; explicit approved state is required")
    if record.run.status.value != "ready_for_approval" or not record.run.approval_required:
        return _invalidate(store, record, lifecycle_path, "stored coding run is no longer approval-ready", now)
    try:
        action = _action_from_queue(queue_path, record.action_id)
    except CodingRunStoreError as exc:
        return _invalidate(store, record, lifecycle_path, str(exc), now)
    if action.status != "approved" or action_fingerprint(action) != record.action_digest:
        return _invalidate(store, record, lifecycle_path, "approved action does not match stored coding run", now)
    try:
        approval = load_approval(approval_dir, action.id)
    except ValueError:
        return _invalidate(store, record, lifecycle_path, "approval record is missing or invalid", now)
    approval_decision = validate_approval(action, approval, now, audit_path)
    if not approval_decision.allowed:
        return _invalidate(store, record, lifecycle_path, approval_decision.reason, now)
    trusted, reason = require_state(lifecycle_path, action.id, LifecycleState.APPROVED)
    if not trusted:
        return _invalidate(store, record, lifecycle_path, reason, now)
    try:
        request = build_approved_coding_request(
            proposal=record.proposal,
            run=record.run,
            action=action,
            approval=approval,
            repository=record.repository,
            base_branch=record.base_branch,
            head_branch=record.head_branch,
            expected_head_sha=record.expected_head_sha,
            claim_store=claim_store,
            now=now,
        )
    except ValueError as exc:
        return _invalidate(store, record, lifecycle_path, str(exc), now)
    try:
        store.begin_execution(record.run_id, now=now)
    except CodingRunStoreError as exc:
        return WorkerResult("blocked", str(exc))
    persisted, reason = record_transition(lifecycle_path, action.id, LifecycleState.APPROVED, LifecycleState.CLAIMED)
    if not persisted:
        try:
            store.mark_failed(record.run_id, worker_state="blocked", reason=reason, now=now)
        except CodingRunStoreError:
            pass
        return WorkerResult("blocked", reason)
    try:
        result = execute_approved_coding_request(request, worker=worker, now=now)
    except Exception as exc:
        result = WorkerResult("failed", f"GitHub worker failed closed: {type(exc).__name__}")
    if result.state != "draft_pr_created":
        try:
            store.mark_failed(record.run_id, worker_state=result.state, reason=result.reason, now=now)
        except CodingRunStoreError:
            pass
        _block_lifecycle(lifecycle_path, action.id)
        return result
    try:
        store.mark_executed(
            record.run_id,
            worker_state=result.state,
            reason=result.reason,
            pull_request=result.pull_request,
            ci_status=result.ci_status,
            now=now,
        )
    except CodingRunStoreError:
        return WorkerResult("blocked", "GitHub draft PR was created but final coding-run state could not be persisted")
    persisted, ledger_reason = record_transition(lifecycle_path, action.id, LifecycleState.CLAIMED, LifecycleState.EXECUTED)
    if not persisted:
        return WorkerResult("draft_pr_created", f"{result.reason}; lifecycle finalization failed: {ledger_reason}", result.draft, result.pull_request, result.ci_status)
    return result
