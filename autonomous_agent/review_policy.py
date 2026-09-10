from __future__ import annotations

from dataclasses import dataclass


REVIEWABLE_PR_STATES = frozenset({"draft", "open"})


@dataclass(frozen=True)
class ReviewPolicyResult:
    allowed: bool
    reason: str
    required_approvals: int
    approvals: int
    unresolved_threads: int


def evaluate_review_policy(
    *,
    pr_state: str,
    ci_passed: bool,
    post_change_verified: bool,
    approvals: int,
    required_approvals: int,
    unresolved_threads: int,
    mergeable: bool,
) -> ReviewPolicyResult:
    """Evaluate whether a reviewed change may cross the final review gate.

    This is a decision-only policy layer: it never merges, deploys, edits, or requests
    reviewers. Missing or malformed policy inputs fail closed.
    """
    try:
        normalized_state = pr_state.strip().lower()
        required = int(required_approvals)
        actual = int(approvals)
        unresolved = int(unresolved_threads)
    except (AttributeError, TypeError, ValueError):
        return ReviewPolicyResult(False, "review policy inputs are invalid", 0, 0, 0)

    if required < 1:
        return ReviewPolicyResult(False, "at least one approval is required", required, actual, unresolved)
    if actual < 0 or unresolved < 0:
        return ReviewPolicyResult(False, "review counts cannot be negative", required, actual, unresolved)
    if normalized_state not in REVIEWABLE_PR_STATES:
        return ReviewPolicyResult(False, f"pull request is not reviewable: {pr_state}", required, actual, unresolved)
    if not ci_passed:
        return ReviewPolicyResult(False, "required CI checks have not passed", required, actual, unresolved)
    if not post_change_verified:
        return ReviewPolicyResult(False, "post-change verification has not passed", required, actual, unresolved)
    if unresolved != 0:
        return ReviewPolicyResult(False, "unresolved review threads remain", required, actual, unresolved)
    if actual < required:
        return ReviewPolicyResult(False, "required human approvals are missing", required, actual, unresolved)
    if not mergeable:
        return ReviewPolicyResult(False, "pull request is not currently mergeable", required, actual, unresolved)

    return ReviewPolicyResult(True, "review policy gate passed; final merge remains a separate operation", required, actual, unresolved)
