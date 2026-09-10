from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Protocol

from .action_queue import PendingAction
from .approved_executor import ApprovalRecord, ExecutionDecision, claim_approval, validate_approval
from .patch_review import PatchReview, review_patch


PROTECTED_BASE_BRANCHES = frozenset({"main", "master", "production", "prod", "release"})
_FORBIDDEN_CHANGE_TERMS = (
    "deploy",
    "release",
    "billing",
    "payment",
    "credential",
    "secret",
    "password",
    "production",
    "delete",
    "remove",
    "uninstall",
)


@dataclass(frozen=True)
class GitHubChangeRequest:
    action_id: str
    repository: str
    base_branch: str
    head_branch: str
    title: str
    body: str
    patch_digest: str
    files: tuple[str, ...]
    additions: int
    deletions: int
    requires_approval: bool = True


@dataclass(frozen=True)
class GitHubChangeResult:
    allowed: bool
    reason: str
    branch: str | None = None
    commit: str | None = None
    pull_request: str | None = None


class GitHubChangeBackend(Protocol):
    """Minimal side-effect boundary for a future GitHub API adapter."""

    def create_branch(self, repository: str, branch: str, base_branch: str) -> str: ...

    def commit_files(
        self,
        repository: str,
        branch: str,
        files: Mapping[str, str],
        message: str,
    ) -> str: ...

    def open_draft_pr(
        self,
        repository: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> str: ...


def _contains_forbidden_term(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _FORBIDDEN_CHANGE_TERMS)


def _validate_files(review: PatchReview, files: Mapping[str, str]) -> str | None:
    supplied = tuple(dict.fromkeys(path.strip().replace("\\", "/").removeprefix("./") for path in files))
    if supplied != review.files:
        return "file manifest does not exactly match the reviewed patch"
    if any("\x00" in content for content in files.values()):
        return "a changed file contains NUL bytes"
    return None


def build_change_request(
    action: PendingAction,
    repository: str,
    base_branch: str,
    head_branch: str,
    title: str,
    body: str,
    unified_diff: str,
) -> GitHubChangeRequest:
    """Create metadata for a GitHub change; this function performs no remote mutation."""
    review = review_patch(unified_diff)
    if not review.allowed:
        raise ValueError(review.reason)
    if base_branch.strip().lower() in PROTECTED_BASE_BRANCHES:
        # Protected bases are allowed as PR targets; protection only forbids direct mutation.
        pass
    if not head_branch.strip() or head_branch.strip().lower() in PROTECTED_BASE_BRANCHES:
        raise ValueError("head branch must be a dedicated non-protected branch")
    if _contains_forbidden_term(" ".join((action.task, title, body))):
        raise ValueError("change request crosses a forbidden capability boundary")
    return GitHubChangeRequest(
        action_id=action.id,
        repository=repository.strip(),
        base_branch=base_branch.strip(),
        head_branch=head_branch.strip(),
        title=title.strip(),
        body=body.strip(),
        patch_digest=review.patch_digest,
        files=review.files,
        additions=review.additions,
        deletions=review.deletions,
        requires_approval=True,
    )


def execute_approved_change(
    action: PendingAction,
    approval: ApprovalRecord,
    request: GitHubChangeRequest,
    unified_diff: str,
    file_contents: Mapping[str, str],
    claim_store: Path,
    backend: GitHubChangeBackend,
    now: datetime | None = None,
    audit_path: Path | None = None,
) -> GitHubChangeResult:
    """Create a branch, one commit and a draft PR only after exact approval is validated and consumed."""
    if action.id != request.action_id:
        return GitHubChangeResult(False, "change request does not match action identity")
    review = review_patch(unified_diff)
    if not review.allowed:
        return GitHubChangeResult(False, review.reason)
    if review.patch_digest != request.patch_digest:
        return GitHubChangeResult(False, "patch digest does not match the reviewed change request")
    manifest_error = _validate_files(review, file_contents)
    if manifest_error:
        return GitHubChangeResult(False, manifest_error)
    if _contains_forbidden_term(" ".join((action.task, request.title, request.body))):
        return GitHubChangeResult(False, "change request crosses a forbidden capability boundary")
    if not request.repository:
        return GitHubChangeResult(False, "repository is required")
    if not request.head_branch or request.head_branch.lower() in PROTECTED_BASE_BRANCHES:
        return GitHubChangeResult(False, "head branch is protected or missing")

    approval_decision: ExecutionDecision = validate_approval(action, approval, now, audit_path)
    if not approval_decision.allowed:
        return GitHubChangeResult(False, approval_decision.reason)
    claimed = claim_approval(approval, claim_store)
    if not claimed.allowed:
        return GitHubChangeResult(False, claimed.reason)

    try:
        branch = backend.create_branch(request.repository, request.head_branch, request.base_branch)
        commit = backend.commit_files(
            request.repository,
            request.head_branch,
            file_contents,
            f"chore: prepare approved change {action.id}",
        )
        pr = backend.open_draft_pr(
            request.repository,
            request.head_branch,
            request.base_branch,
            request.title,
            request.body,
        )
    except Exception as exc:
        return GitHubChangeResult(False, f"GitHub change operation failed closed: {exc}")

    return GitHubChangeResult(True, "approved change prepared as a draft pull request", branch, commit, pr)
