from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autonomous_agent.action_queue import PendingAction
from autonomous_agent.approved_executor import ApprovalRecord
from autonomous_agent.capability_policy import Capability, check_capability
from autonomous_agent.draft_pr_automation import (
    DraftPrPreparationError,
    build_draft_pr_request,
    prepare_draft_pr,
    proposal_pr_body,
    proposal_pr_title,
)


SHA = "a" * 40
DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print('old')
+print('new')
"""
FILES = {"app.py": "print('new')\n"}


class Head:
    def __init__(self, value=SHA):
        self.value = value

    def head_sha(self, repository, branch):
        return self.value


class PRs:
    def __init__(self, value=None):
        self.value = value

    def find(self, repository, head_branch, base_branch, patch_digest):
        return self.value


class Backend:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def create_branch(self, repository, branch, base_branch):
        self.calls.append(("branch", repository, branch, base_branch))
        if self.fail_at == "branch":
            raise RuntimeError("interrupted")
        return branch

    def commit_files(self, repository, branch, files, message):
        self.calls.append(("commit", repository, branch, dict(files), message))
        if self.fail_at == "commit":
            raise RuntimeError("interrupted")
        return "b" * 40

    def open_draft_pr(self, repository, head_branch, base_branch, title, body):
        self.calls.append(("pr", repository, head_branch, base_branch, title, body))
        if self.fail_at == "pr":
            raise RuntimeError("interrupted")
        return "https://github.com/example/repo/pull/1"


def action(status="approved"):
    return PendingAction("action-1", "inspect and prepare source change", ("inspect",), "high", "approved improvement", status, "2026-09-11T10:00:00+00:00")


def approval_for(item, *, expired=False):
    approved = datetime.now(timezone.utc) - timedelta(hours=25 if expired else 1)
    return ApprovalRecord.for_action(item, "approval-token", approved_at=approved)


def make_request(item, approval, tmp_path, *, diff=DIFF, repository="owner/repo", base="main", head="improvement/one"):
    return build_draft_pr_request(
        item, approval, "proposal-fingerprint", repository, base, head, SHA,
        proposal_pr_title("test regression", "owner/repo"),
        proposal_pr_body("proposal-fingerprint", ("CI failure",), ("run tests",)),
        diff, FILES, now=datetime.now(timezone.utc),
    )


def test_valid_approved_draft_pr_preparation(tmp_path):
    item = action()
    approval = approval_for(item)
    request = make_request(item, approval, tmp_path)
    result = prepare_draft_pr(item, approval, request, DIFF, FILES, head_provider=Head(), existing_prs=PRs(), backend=Backend(), claim_store=tmp_path / "claims", now=datetime.now(timezone.utc))
    assert result.allowed is True
    assert result.github_result and result.github_result.pull_request


def test_unapproved_proposal_fails_closed(tmp_path):
    item = action("pending")
    approval = approval_for(item)
    with pytest.raises(DraftPrPreparationError, match="not explicitly approved"):
        make_request(item, approval, tmp_path)


def test_stale_approval_fails_closed(tmp_path):
    item = action()
    approval = approval_for(item, expired=True)
    with pytest.raises(DraftPrPreparationError, match="expired"):
        make_request(item, approval, tmp_path)


def test_head_mismatch_fails_before_remote_mutation(tmp_path):
    item = action()
    approval = approval_for(item)
    request = make_request(item, approval, tmp_path)
    backend = Backend()
    result = prepare_draft_pr(item, approval, request, DIFF, FILES, head_provider=Head("b" * 40), existing_prs=PRs(), backend=backend, claim_store=tmp_path / "claims", now=datetime.now(timezone.utc))
    assert result.allowed is False
    assert "HEAD mismatch" in result.reason
    assert backend.calls == []


def test_patch_fingerprint_mismatch_fails_closed(tmp_path):
    item = action()
    approval = approval_for(item)
    request = make_request(item, approval, tmp_path)
    changed = DIFF.replace("new", "changed")
    result = prepare_draft_pr(item, approval, request, changed, FILES, head_provider=Head(), existing_prs=PRs(), backend=Backend(), claim_store=tmp_path / "claims", now=datetime.now(timezone.utc))
    assert result.allowed is False
    assert "patch fingerprint mismatch" in result.reason


def test_duplicate_pr_fails_closed(tmp_path):
    item = action()
    approval = approval_for(item)
    request = make_request(item, approval, tmp_path)
    backend = Backend()
    result = prepare_draft_pr(item, approval, request, DIFF, FILES, head_provider=Head(), existing_prs=PRs("https://github.com/owner/repo/pull/9"), backend=backend, claim_store=tmp_path / "claims", now=datetime.now(timezone.utc))
    assert result.allowed is False
    assert "duplicate" in result.reason
    assert backend.calls == []


def test_repository_mismatch_fails_closed(tmp_path):
    item = action()
    approval = approval_for(item)
    with pytest.raises(DraftPrPreparationError, match="owner/repository"):
        make_request(item, approval, tmp_path, repository="not-a-repository")


def test_target_branch_mismatch_is_rejected(tmp_path):
    item = action()
    approval = approval_for(item)
    with pytest.raises(DraftPrPreparationError, match="dedicated non-protected"):
        make_request(item, approval, tmp_path, base="main", head="main")


def test_unauthorized_write_cannot_use_pending_action(tmp_path):
    item = action("pending")
    approval = approval_for(item)
    with pytest.raises(DraftPrPreparationError):
        make_request(item, approval, tmp_path)


def test_permanent_capability_denial_remains_intact():
    decision = check_capability(Capability.SOURCE_WRITE, [Capability.SOURCE_WRITE])
    assert decision.allowed is False
    assert "permanently denied" in decision.reason


def test_secret_leakage_is_redacted():
    title = proposal_pr_title("api_key=supersecret", "owner/repo")
    body = proposal_pr_body("abc", ("token=supersecret", "-----BEGIN PRIVATE KEY-----secret-----END PRIVATE KEY-----"), ("run tests",))
    assert "supersecret" not in title
    assert "supersecret" not in body
    assert "[REDACTED]" in title
    assert "[REDACTED]" in body


def test_replay_attempt_is_rejected_after_approval_claim(tmp_path):
    item = action()
    approval = approval_for(item)
    request = make_request(item, approval, tmp_path)
    claims = tmp_path / "claims"
    first = prepare_draft_pr(item, approval, request, DIFF, FILES, head_provider=Head(), existing_prs=PRs(), backend=Backend(), claim_store=claims, now=datetime.now(timezone.utc))
    assert first.allowed
    second = prepare_draft_pr(item, approval, request, DIFF, FILES, head_provider=Head(), existing_prs=PRs(), backend=Backend(), claim_store=claims, now=datetime.now(timezone.utc))
    assert second.allowed is False
    assert "consumed" in second.reason


def test_interrupted_operation_fails_closed_and_does_not_replay(tmp_path):
    item = action()
    approval = approval_for(item)
    request = make_request(item, approval, tmp_path)
    claims = tmp_path / "claims"
    interrupted = prepare_draft_pr(item, approval, request, DIFF, FILES, head_provider=Head(), existing_prs=PRs(), backend=Backend("commit"), claim_store=claims, now=datetime.now(timezone.utc))
    assert interrupted.allowed is False
    replay = prepare_draft_pr(item, approval, request, DIFF, FILES, head_provider=Head(), existing_prs=PRs(), backend=Backend(), claim_store=claims, now=datetime.now(timezone.utc))
    assert replay.allowed is False
    assert "consumed" in replay.reason


def test_file_contents_fingerprint_mismatch_fails_closed(tmp_path):
    item = action()
    approval = approval_for(item)
    request = make_request(item, approval, tmp_path)
    result = prepare_draft_pr(item, approval, request, DIFF, {"app.py": "print('tampered')\n"}, head_provider=Head(), existing_prs=PRs(), backend=Backend(), claim_store=tmp_path / "claims", now=datetime.now(timezone.utc))
    assert result.allowed is False
    assert "contents fingerprint mismatch" in result.reason


def test_request_fingerprint_tampering_fails_closed(tmp_path):
    item = action()
    approval = approval_for(item)
    request = make_request(item, approval, tmp_path)
    tampered = type(request)(**{**request.__dict__, "proposal_fingerprint": "other"})
    result = prepare_draft_pr(item, approval, tampered, DIFF, FILES, head_provider=Head(), existing_prs=PRs(), backend=Backend(), claim_store=tmp_path / "claims", now=datetime.now(timezone.utc))
    assert result.allowed is False
    assert "request fingerprint mismatch" in result.reason
