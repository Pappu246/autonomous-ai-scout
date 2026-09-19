from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .action_queue import PendingAction
from .approved_executor import ApprovalRecord
from .draft_pr_automation import (
    DraftPrRequest,
    DraftPrResult,
    ExistingPrLookup,
    HeadShaProvider,
    prepare_draft_pr,
)
from .self_improvement import PatchCandidate


class PullRequestProvider(Protocol):
    def get(self, repository: str, pull_request: str) -> dict: ...


class CiStatusProvider(Protocol):
    def status(self, repository: str, pull_request: str) -> str: ...


@dataclass(frozen=True)
class WorkerRequest:
    repository: str
    base_branch: str
    head_branch: str
    pull_request_title: str
    pull_request_body: str
    proposal_fingerprint: str
    action: PendingAction
    approval: ApprovalRecord
    patch: PatchCandidate
    draft_request: DraftPrRequest


@dataclass(frozen=True)
class WorkerResult:
    state: str
    reason: str
    draft: DraftPrResult | None = None
    pull_request: str | None = None
    ci_status: str | None = None


TERMINAL_CI = frozenset({"success", "failure", "cancelled", "timed_out", "action_required"})
ACTIVE_CI = frozenset({"queued", "in_progress", "pending", "waiting"})


class GitHubWorker:
    """Execute one explicitly approved patch through the GitHub PR boundary.

    This worker never merges or deploys. Remote mutation is delegated to the
    existing GitHubChangeBackend through prepare_draft_pr, and every invocation
    re-validates the approval, patch digest, file manifest, target HEAD, and
    duplicate-PR guard.
    """

    def __init__(
        self,
        *,
        head_provider: HeadShaProvider,
        existing_prs: ExistingPrLookup,
        backend,
        claim_store,
        pull_requests: PullRequestProvider,
        ci: CiStatusProvider,
    ):
        self.head_provider = head_provider
        self.existing_prs = existing_prs
        self.backend = backend
        self.claim_store = claim_store
        self.pull_requests = pull_requests
        self.ci = ci

    def execute(self, request: WorkerRequest, *, now=None) -> WorkerResult:
        patch = request.patch
        result = prepare_draft_pr(
            request.action,
            request.approval,
            request.draft_request,
            patch.unified_diff,
            patch.file_contents,
            head_provider=self.head_provider,
            existing_prs=self.existing_prs,
            backend=self.backend,
            claim_store=self.claim_store,
            now=now,
        )
        if not result.allowed:
            return WorkerResult("blocked", result.reason, result)

        pr = result.github_result.pull_request if result.github_result else None
        if not pr:
            return WorkerResult("blocked", "GitHub backend did not return a pull request", result)

        return WorkerResult("draft_pr_created", "approved patch was committed and opened as a draft PR", result, pr)

    def observe(self, repository: str, pull_request: str) -> WorkerResult:
        try:
            snapshot = self.pull_requests.get(repository, pull_request)
            ci_status = self.ci.status(repository, pull_request)
        except Exception as exc:
            return WorkerResult("observation_failed", f"GitHub observation failed closed: {type(exc).__name__}", pull_request=pull_request)

        state = str(snapshot.get("state", "unknown")).lower()
        merged = bool(snapshot.get("merged", False))
        draft = bool(snapshot.get("draft", False))

        if merged:
            return WorkerResult("merged_observed", "merge was observed externally; worker did not perform it", pull_request=pull_request, ci_status=ci_status)
        if state == "closed":
            return WorkerResult("closed_observed", "pull request was closed externally", pull_request=pull_request, ci_status=ci_status)
        if ci_status in {"failure", "cancelled", "timed_out", "action_required"}:
            return WorkerResult("ci_failed", f"CI status is {ci_status}; no mutation performed", pull_request=pull_request, ci_status=ci_status)
        if ci_status in ACTIVE_CI:
            return WorkerResult("ci_running", "CI is still running; no mutation performed", pull_request=pull_request, ci_status=ci_status)
        if draft:
            return WorkerResult("draft_pr_ready", "draft PR exists and requires human review", pull_request=pull_request, ci_status=ci_status)
        return WorkerResult("review_ready", "open PR observed; human review/merge remains external", pull_request=pull_request, ci_status=ci_status)
