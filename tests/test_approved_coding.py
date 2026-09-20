from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autonomous_agent.action_queue import PendingAction
from autonomous_agent.approved_coding import (
    build_approved_coding_request,
    execute_approved_coding_request,
)
from autonomous_agent.approved_executor import ApprovalRecord
from autonomous_agent.continuous_improvement import build_proposal
from autonomous_agent.draft_pr_automation import build_draft_pr_request
from autonomous_agent.models import ProjectFinding
from autonomous_agent.self_improvement import (
    ImprovementRun,
    ImprovementStatus,
    PatchCandidate,
    ValidationResult,
)
from autonomous_agent.patch_review import review_patch


DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print("old")
+print("new")
"""
FILES = {"app.py": 'print("new")\n'}
SHA = "a" * 40


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


def action():
    return PendingAction(
        "action-gateway",
        "prepare approved source improvement",
        ("inspect", "test"),
        "high",
        "approved improvement",
        "approved",
        "2026-09-21T00:00:00+00:00",
    )


def approved(item):
    now = datetime.now(timezone.utc)
    return ApprovalRecord.for_action(item, "gateway-approval", now, timedelta(hours=1))


def run_ready():
    review = review_patch(DIFF)
    return ImprovementRun(
        ImprovementStatus.READY_FOR_APPROVAL,
        proposal().fingerprint,
        (),
        PatchCandidate(DIFF, FILES, "fix regression", ("python -m pytest -q",)),
        review,
        "ready",
        True,
    )


class Head:
    def head_sha(self, repository, branch):
        return SHA


class PRs:
    def find(self, repository, head_branch, base_branch, patch_digest):
        return None


class Backend:
    def __init__(self):
        self.calls = []

    def create_branch(self, repository, branch, base_branch):
        return self.create_branch_at_sha(repository, branch, base_branch, SHA)

    def create_branch_at_sha(self, repository, branch, base_branch, expected_head_sha):
        self.calls.append(("branch", repository, branch, base_branch, expected_head_sha))
        return branch

    def commit_files(self, repository, branch, files, message):
        self.calls.append(("commit", repository, branch))
        return "b" * 40

    def open_draft_pr(self, repository, head_branch, base_branch, title, body):
        self.calls.append(("pr", repository, head_branch, base_branch))
        return "https://github.com/owner/repo/pull/1"


def worker_for(tmp_path):
    from autonomous_agent.github_worker import GitHubWorker
    return GitHubWorker(
        head_provider=Head(),
        existing_prs=PRs(),
        backend=Backend(),
        claim_store=tmp_path / "claims",
        pull_requests=type("PRProvider", (), {"get": lambda self, r, p: {"state": "open", "draft": True, "merged": False}})(),
        ci=type("CI", (), {"status": lambda self, r, p: "queued"})(),
    )


def test_build_requires_ready_approval_state(tmp_path):
    item = proposal()
    invalid = ImprovementRun(
        ImprovementStatus.VALIDATION_FAILED,
        item.fingerprint,
        (),
        None,
        None,
        "failed",
        True,
    )
    with pytest.raises(ValueError, match="not ready"):
        build_approved_coding_request(
            proposal=item,
            run=invalid,
            action=action(),
            approval=approved(action()),
            repository="owner/repo",
            base_branch="main",
            head_branch="improvement/gateway",
            expected_head_sha=SHA,
            claim_store=tmp_path / "claims",
        )


def test_gateway_revalidates_candidate_and_stays_approval_gated(tmp_path):
    item = proposal()
    act = action()
    app = approved(act)
    request = build_approved_coding_request(
        proposal=item,
        run=run_ready(),
        action=act,
        approval=app,
        repository="owner/repo",
        base_branch="main",
        head_branch="improvement/gateway",
        expected_head_sha=SHA,
        claim_store=tmp_path / "claims",
    )
    assert request.run.status is ImprovementStatus.READY_FOR_APPROVAL

    result = execute_approved_coding_request(
        request,
        worker=worker_for(tmp_path),
        now=datetime.now(timezone.utc),
    )
    assert result.state == "draft_pr_created"
    assert result.pull_request.endswith("/pull/1")


def test_gateway_rejects_repository_mismatch(tmp_path):
    item = proposal()
    act = action()
    app = approved(act)
    with pytest.raises(ValueError, match="does not match"):
        build_approved_coding_request(
            proposal=item,
            run=run_ready(),
            action=act,
            approval=app,
            repository="other/repo",
            base_branch="main",
            head_branch="improvement/gateway",
            expected_head_sha=SHA,
            claim_store=tmp_path / "claims",
        )
