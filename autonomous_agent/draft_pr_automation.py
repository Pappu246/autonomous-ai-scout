from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Mapping, Protocol

from .action_queue import PendingAction
from .approved_executor import ApprovalRecord, action_fingerprint, validate_approval
from .github_changes import GitHubChangeBackend, GitHubChangeResult, build_change_request, execute_approved_change
from .patch_review import PatchReview, review_patch


_OWNER_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SECRET = re.compile(r"(?i)(?:api[_-]?key|api\s+key|access[_-]?token|access\s+token|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----.*?-----END [A-Z0-9 ]+PRIVATE KEY-----", re.S)
_PROTECTED = frozenset({"main", "master", "production", "prod", "release"})


class DraftPrPreparationError(ValueError):
    pass


class HeadShaProvider(Protocol):
    def head_sha(self, repository: str, branch: str) -> str | None: ...


class ExistingPrLookup(Protocol):
    def find(self, repository: str, head_branch: str, base_branch: str, patch_digest: str) -> str | None: ...


@dataclass(frozen=True)
class DraftPrRequest:
    action_id: str
    proposal_fingerprint: str
    repository: str
    base_branch: str
    head_branch: str
    expected_head_sha: str
    patch_digest: str
    file_contents_digest: str
    title: str
    body: str
    files: tuple[str, ...]
    additions: int
    deletions: int
    request_fingerprint: str


@dataclass(frozen=True)
class DraftPrResult:
    allowed: bool
    reason: str
    request_fingerprint: str
    github_result: GitHubChangeResult | None = None


def _safe(value: object, limit: int = 512) -> str:
    text = _PRIVATE_KEY.sub("[REDACTED]", str(value))
    return _SECRET.sub("[REDACTED]", text)[:limit]


def _content_digest(file_contents: Mapping[str, str]) -> str:
    payload = [(path.strip().replace("\\", "/").removeprefix("./"), str(content)) for path, content in file_contents.items()]
    payload.sort(key=lambda item: item[0])
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _request_digest(request: DraftPrRequest) -> str:
    payload = {
        "action_id": request.action_id,
        "proposal_fingerprint": request.proposal_fingerprint,
        "repository": request.repository,
        "base_branch": request.base_branch,
        "head_branch": request.head_branch,
        "expected_head_sha": request.expected_head_sha,
        "patch_digest": request.patch_digest,
        "file_contents_digest": request.file_contents_digest,
        "title": request.title,
        "body": request.body,
        "files": request.files,
        "additions": request.additions,
        "deletions": request.deletions,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_identity(repository: str, base_branch: str, head_branch: str, expected_head_sha: str) -> str | None:
    if not _OWNER_REPOSITORY.fullmatch(repository.strip()):
        return "repository must use owner/repository identity"
    if not base_branch.strip():
        return "target branch is required"
    if not head_branch.strip() or head_branch.strip().lower() in _PROTECTED:
        return "dedicated non-protected head branch is required"
    if not expected_head_sha or not re.fullmatch(r"[0-9a-fA-F]{40}", expected_head_sha.strip()):
        return "expected HEAD SHA is required"
    return None


def build_draft_pr_request(
    action: PendingAction,
    approval: ApprovalRecord,
    proposal_fingerprint: str,
    repository: str,
    base_branch: str,
    head_branch: str,
    expected_head_sha: str,
    title: str,
    body: str,
    unified_diff: str,
    file_contents: Mapping[str, str],
    *,
    now=None,
) -> DraftPrRequest:
    identity_error = _validate_identity(repository, base_branch, head_branch, expected_head_sha)
    if identity_error:
        raise DraftPrPreparationError(identity_error)
    if action.status != "approved":
        raise DraftPrPreparationError("proposal action is not explicitly approved")
    approval_decision = validate_approval(action, approval, now)
    if not approval_decision.allowed:
        raise DraftPrPreparationError(approval_decision.reason)
    if not proposal_fingerprint.strip():
        raise DraftPrPreparationError("proposal fingerprint is required")
    if approval.action_digest != action_fingerprint(action):
        raise DraftPrPreparationError("approval is not bound to the current action")
    review: PatchReview = review_patch(unified_diff)
    if not review.allowed:
        raise DraftPrPreparationError(review.reason)
    safe_title, safe_body = _safe(title), _safe(body, 4000)
    if not safe_title.strip():
        raise DraftPrPreparationError("PR title is required")
    request = DraftPrRequest(
        action_id=action.id,
        proposal_fingerprint=proposal_fingerprint.strip(),
        repository=repository.strip(),
        base_branch=base_branch.strip(),
        head_branch=head_branch.strip(),
        expected_head_sha=expected_head_sha.strip().lower(),
        patch_digest=review.patch_digest,
        file_contents_digest=_content_digest(file_contents),
        title=safe_title,
        body=safe_body,
        files=review.files,
        additions=review.additions,
        deletions=review.deletions,
        request_fingerprint="",
    )
    return replace(request, request_fingerprint=_request_digest(request))


def prepare_draft_pr(
    action: PendingAction,
    approval: ApprovalRecord,
    request: DraftPrRequest,
    unified_diff: str,
    file_contents: Mapping[str, str],
    *,
    head_provider: HeadShaProvider,
    existing_prs: ExistingPrLookup,
    backend: GitHubChangeBackend,
    claim_store,
    audit_path=None,
    now=None,
) -> DraftPrResult:
    unsigned = replace(request, request_fingerprint="")
    if request.request_fingerprint != _request_digest(unsigned):
        return DraftPrResult(False, "draft request fingerprint mismatch", request.request_fingerprint)
    if action.id != request.action_id:
        return DraftPrResult(False, "draft request does not match action identity", request.request_fingerprint)
    if approval.action_id != action.id or approval.action_digest != action_fingerprint(action):
        return DraftPrResult(False, "approval identity does not match the approved action", request.request_fingerprint)
    decision = validate_approval(action, approval, now, audit_path)
    if not decision.allowed:
        return DraftPrResult(False, decision.reason, request.request_fingerprint)
    identity_error = _validate_identity(request.repository, request.base_branch, request.head_branch, request.expected_head_sha)
    if identity_error:
        return DraftPrResult(False, identity_error, request.request_fingerprint)
    review = review_patch(unified_diff)
    if not review.allowed:
        return DraftPrResult(False, review.reason, request.request_fingerprint)
    if review.patch_digest != request.patch_digest:
        return DraftPrResult(False, "patch fingerprint mismatch", request.request_fingerprint)
    supplied_files = tuple(dict.fromkeys(path.strip().replace("\\", "/").removeprefix("./") for path in file_contents))
    if supplied_files != request.files:
        return DraftPrResult(False, "approved patch file manifest mismatch", request.request_fingerprint)
    if _content_digest(file_contents) != request.file_contents_digest:
        return DraftPrResult(False, "approved file contents fingerprint mismatch", request.request_fingerprint)
    if any("\x00" in content for content in file_contents.values()):
        return DraftPrResult(False, "changed file contains NUL bytes", request.request_fingerprint)
    current_sha = head_provider.head_sha(request.repository, request.base_branch)
    if current_sha is None:
        return DraftPrResult(False, "target branch HEAD could not be verified", request.request_fingerprint)
    if current_sha.lower() != request.expected_head_sha.lower():
        return DraftPrResult(False, "target branch HEAD mismatch; approval is stale", request.request_fingerprint)
    duplicate = existing_prs.find(request.repository, request.head_branch, request.base_branch, request.patch_digest)
    if duplicate:
        return DraftPrResult(False, "duplicate draft PR already exists", request.request_fingerprint)
    change_request = build_change_request(
        action,
        request.repository,
        request.base_branch,
        request.head_branch,
        request.title,
        request.body,
        unified_diff,
    )
    if change_request.patch_digest != request.patch_digest or change_request.action_id != request.action_id:
        return DraftPrResult(False, "existing change boundary rejected request identity", request.request_fingerprint)
    result = execute_approved_change(
        action,
        approval,
        change_request,
        unified_diff,
        file_contents,
        claim_store,
        backend,
        now=now,
        audit_path=audit_path,
    )
    return DraftPrResult(result.allowed, result.reason, request.request_fingerprint, result)


def proposal_pr_title(problem: str, project: str) -> str:
    return _safe(f"improvement: {problem} [{project}]")[:120]


def proposal_pr_body(proposal_fingerprint: str, evidence: tuple[str, ...], validation: tuple[str, ...]) -> str:
    safe_evidence = "\n".join(f"- {_safe(item)}" for item in evidence[:10])
    safe_validation = "\n".join(f"- {_safe(item)}" for item in validation[:10])
    return (
        "Prepared from an approved improvement proposal.\n\n"
        f"Proposal fingerprint: {_safe(proposal_fingerprint, 128)}\n\n"
        "Evidence:\n" + safe_evidence + "\n\n"
        "Validation plan:\n" + safe_validation + "\n\n"
        "This draft contains only the approved reviewed patch. Further review is required before any protected action."
    )
