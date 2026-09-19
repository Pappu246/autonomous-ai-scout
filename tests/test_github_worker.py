from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autonomous_agent.action_queue import PendingAction
from autonomous_agent.approved_executor import ApprovalRecord
from autonomous_agent.draft_pr_automation import build_draft_pr_request, proposal_pr_body, proposal_pr_title
from autonomous_agent.github_worker import GitHubWorker, WorkerRequest
from autonomous_agent.self_improvement import PatchCandidate


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
    def head_sha(self, repository, branch):
        return SHA


class PRs:
    def find(self, repository, head_branch, base_branch, patch_digest):
        return None


class Backend:
    def __init__(self):
        self.calls = []

    def create_branch(self, repository, branch, base_branch):
        self.calls.append(("branch", repository, branch, base_branch))
        return branch

    def commit_files(self, repository, branch, files, message):
        self.calls.append(("commit", repository, branch))
        return "b" * 40

    def open_draft_pr(self, repository, head_branch, base_branch, title, body):
        self.calls.append(("pr", repository, head_branch))
        return "https://github.com/owner/repo/pull/42"


class PRProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get(self, repository, pull_request):
        return self.snapshot


class CI:
    def __init__(self, status):
        self.value = status

    def status(self, repository, pull_request):
        return self.value


def action():
    return PendingAction("action-n12", "prepare approved source improvement", ("inspect", "test"), "high", "approved", "approved", "2026-09-19T10:00:00+00:00")


def approval(item):
    return ApprovalRecord.for_action(item, "approval", datetime.now(timezone.utc), timedelta(hours=1))


def worker_request(tmp_path):
    item = action()
    approved = approval(item)
    request = build_draft_pr_request(
        item,
        approved,
        "proposal-n12",
        "owner/repo",
        "main",
        "improvement/action-n12",
        SHA,
        proposal_pr_title("regression fix", "owner/repo"),
        proposal_pr_body("proposal-n12", ("CI regression",), ("pytest -q",)),
        DIFF,
        FILES,
        now=datetime.now(timezone.utc),
    )
    patch = PatchCandidate(DIFF, FILES, "regression fix", ("pytest -q",))
    return WorkerRequest("owner/repo", "main", "improvement/action-n12", request.title, request.body, "proposal-n12", item, approved, patch, request)


def test_worker_creates_draft_pr_after_approved_gate(tmp_path):
    backend = Backend()
    req = worker_request(tmp_path)
    worker = GitHubWorker(
        head_provider=Head(),
        existing_prs=PRs(),
        backend=backend,
        claim_store=tmp_path / "claims",
        pull_requests=PRProvider({"state": "open", "draft": True, "merged": False}),
        ci=CI("queued"),
    )
    result = worker.execute(req, now=datetime.now(timezone.utc))
    assert result.state == "draft_pr_created"
    assert result.pull_request.endswith("/42")
    assert [x[0] for x in backend.calls] == ["branch", "commit", "pr"]


def test_worker_replay_is_blocked_by_single_use_approval(tmp_path):
    req = worker_request(tmp_path)
    backend = Backend()
    worker = GitHubWorker(
        head_provider=Head(),
        existing_prs=PRs(),
        backend=backend,
        claim_store=tmp_path / "claims",
        pull_requests=PRProvider({"state": "open", "draft": True, "merged": False}),
        ci=CI("queued"),
    )
    first = worker.execute(req, now=datetime.now(timezone.utc))
    second = worker.execute(req, now=datetime.now(timezone.utc))
    assert first.state == "draft_pr_created"
    assert second.state == "blocked"
    assert "consumed" in second.reason


def test_worker_observes_running_ci_without_mutation(tmp_path):
    worker = GitHubWorker(
        head_provider=Head(),
        existing_prs=PRs(),
        backend=Backend(),
        claim_store=tmp_path / "claims",
        pull_requests=PRProvider({"state": "open", "draft": True, "merged": False}),
        ci=CI("in_progress"),
    )
    result = worker.observe("owner/repo", "https://github.com/owner/repo/pull/42")
    assert result.state == "ci_running"


def test_worker_stops_on_failed_ci(tmp_path):
    worker = GitHubWorker(
        head_provider=Head(),
        existing_prs=PRs(),
        backend=Backend(),
        claim_store=tmp_path / "claims",
        pull_requests=PRProvider({"state": "open", "draft": False, "merged": False}),
        ci=CI("failure"),
    )
    result = worker.observe("owner/repo", "https://github.com/owner/repo/pull/42")
    assert result.state == "ci_failed"
    assert result.ci_status == "failure"


def test_worker_never_merges(tmp_path):
    worker = GitHubWorker(
        head_provider=Head(),
        existing_prs=PRs(),
        backend=Backend(),
        claim_store=tmp_path / "claims",
        pull_requests=PRProvider({"state": "open", "draft": False, "merged": False}),
        ci=CI("success"),
    )
    result = worker.observe("owner/repo", "https://github.com/owner/repo/pull/42")
    assert result.state == "review_ready"
    assert "external" in result.reason
