from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .approved_executor import ApprovalRecord
from .continuous_improvement import ImprovementProposal
from .draft_pr_automation import (
    DraftPrRequest,
    DraftPrResult,
    build_draft_pr_request,
    proposal_pr_body,
    proposal_pr_title,
)
from .github_worker import GitHubWorker, WorkerRequest, WorkerResult
from .self_improvement import ImprovementRun, ImprovementStatus


@dataclass(frozen=True)
class ApprovedCodingRequest:
    """Exact inputs needed to hand a validated candidate to the GitHub worker."""

    proposal: ImprovementProposal
    run: ImprovementRun
    action: object
    approval: ApprovalRecord
    repository: str
    base_branch: str
    head_branch: str
    expected_head_sha: str
    claim_store: Path


def build_approved_coding_request(
    *,
    proposal: ImprovementProposal,
    run: ImprovementRun,
    action,
    approval: ApprovalRecord,
    repository: str,
    base_branch: str,
    head_branch: str,
    expected_head_sha: str,
    claim_store: Path,
    now: datetime | None = None,
) -> ApprovedCodingRequest:
    if run.status is not ImprovementStatus.READY_FOR_APPROVAL:
        raise ValueError("coding run is not ready for approval")
    if run.candidate is None or run.review is None:
        raise ValueError("approved coding request requires a reviewed patch candidate")
    if not run.approval_required:
        raise ValueError("coding request must remain approval-gated")
    if repository.strip() != proposal.project.strip():
        raise ValueError("repository does not match improvement proposal")
    if action.status != "approved":
        raise ValueError("approved action is required")

    # Reuse the existing exact review/identity/digest boundary. This validates
    # the candidate again rather than trusting the earlier in-memory result.
    draft: DraftPrRequest = build_draft_pr_request(
        action,
        approval,
        proposal.fingerprint,
        repository,
        base_branch,
        head_branch,
        expected_head_sha,
        proposal_pr_title(proposal.problem, proposal.project),
        proposal_pr_body(
            proposal.fingerprint,
            tuple(item.summary for item in proposal.evidence),
            proposal.validation_strategy,
        ),
        run.candidate.unified_diff,
        run.candidate.file_contents,
        now=now,
    )
    return ApprovedCodingRequest(
        proposal=proposal,
        run=run,
        action=action,
        approval=approval,
        repository=repository,
        base_branch=base_branch,
        head_branch=head_branch,
        expected_head_sha=expected_head_sha,
        claim_store=claim_store,
    )


def execute_approved_coding_request(
    request: ApprovedCodingRequest,
    *,
    worker: GitHubWorker,
    now: datetime | None = None,
) -> WorkerResult:
    candidate = request.run.candidate
    if candidate is None:
        return WorkerResult("blocked", "approved coding request has no candidate")

    draft = build_draft_pr_request(
        request.action,
        request.approval,
        request.proposal.fingerprint,
        request.repository,
        request.base_branch,
        request.head_branch,
        request.expected_head_sha,
        proposal_pr_title(request.proposal.problem, request.proposal.project),
        proposal_pr_body(
            request.proposal.fingerprint,
            tuple(item.summary for item in request.proposal.evidence),
            request.proposal.validation_strategy,
        ),
        candidate.unified_diff,
        candidate.file_contents,
        now=now,
    )

    worker_request = WorkerRequest(
        repository=request.repository,
        base_branch=request.base_branch,
        head_branch=request.head_branch,
        pull_request_title=draft.title,
        pull_request_body=draft.body,
        proposal_fingerprint=request.proposal.fingerprint,
        action=request.action,
        approval=request.approval,
        patch=candidate,
        draft_request=draft,
    )
    return worker.execute(worker_request, now=now)
