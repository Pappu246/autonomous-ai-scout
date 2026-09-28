"""Phase 8 — controlled release-boundary decision gate.

This module is decision-only. It never deploys, merges, mutates infrastructure,
accesses credentials, or grants a new execution capability.

The gate composes independently acquired evidence:
    artifact identity
    exact Phase 6 post-change verification
    review-policy result
    CI result
    explicit release approval
and returns only an eligibility decision for an allowlisted non-production target.

Production release and deployment remain outside this boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Iterable

from .post_change_final import Phase6VerificationReport
from .review_policy import ReviewPolicyResult


SAFE_RELEASE_TARGETS: Final[frozenset[str]] = frozenset({"sandbox", "staging"})
FORBIDDEN_RELEASE_TARGETS: Final[frozenset[str]] = frozenset({"production", "prod"})
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ReleaseBoundaryResult:
    allowed: bool
    reason: str
    target: str
    artifact_digest: str
    verification_digest: str
    deployment_permitted: bool = False


def _normalize_targets(values: Iterable[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("release targets must be strings")
        item = value.strip().lower()
        if item:
            normalized.add(item)
    return frozenset(normalized)


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value.strip().lower()))


def _fail(
    reason: str,
    *,
    target: str = "",
    artifact_digest: str = "",
    verification_digest: str = "",
) -> ReleaseBoundaryResult:
    return ReleaseBoundaryResult(
        False,
        reason,
        target,
        artifact_digest,
        verification_digest,
        False,
    )


def evaluate_release_boundary(
    *,
    artifact_digest: str,
    expected_artifact_digest: str,
    target: str,
    ci_passed: bool,
    post_change_report: Phase6VerificationReport | None,
    expected_request_digest: str,
    review_policy: ReviewPolicyResult | None,
    release_approved: bool,
    allowed_targets: Iterable[str] = SAFE_RELEASE_TARGETS,
) -> ReleaseBoundaryResult:
    """Return deterministic eligibility for a controlled non-production release.

    Every prerequisite is evaluated fail-closed. A successful result means only
    that a release request satisfies the policy boundary; it does not authorize
    or perform a deployment.
    """
    if not isinstance(artifact_digest, str) or not isinstance(expected_artifact_digest, str):
        return _fail("artifact digest inputs are invalid")
    if not isinstance(expected_request_digest, str):
        return _fail("expected request digest is invalid")
    if not isinstance(target, str):
        return _fail("release target is invalid")
    if ci_passed is not True:
        return _fail("required CI checks have not passed")
    if release_approved is not True:
        return _fail("explicit release approval is missing")

    normalized_target = target.strip().lower()
    actual_artifact = artifact_digest.strip().lower()
    expected_artifact = expected_artifact_digest.strip().lower()
    expected_request = expected_request_digest.strip().lower()

    if not normalized_target:
        return _fail("release target is required")
    if normalized_target in FORBIDDEN_RELEASE_TARGETS:
        return _fail("production release is outside the autonomous boundary", target=normalized_target)
    try:
        normalized_allowed = _normalize_targets(allowed_targets)
    except (TypeError, ValueError):
        return _fail("release target allowlist is invalid", target=normalized_target)
    if normalized_target not in normalized_allowed:
        return _fail("release target is not allowlisted", target=normalized_target)
    if not _valid_digest(actual_artifact) or not _valid_digest(expected_artifact):
        return _fail("artifact digest must be a SHA-256 hex digest", target=normalized_target)
    if actual_artifact != expected_artifact:
        return _fail(
            "release artifact digest does not match",
            target=normalized_target,
            artifact_digest=actual_artifact,
        )
    if not _valid_digest(expected_request):
        return _fail("expected request digest must be a SHA-256 hex digest", target=normalized_target)
    if post_change_report is None or not isinstance(post_change_report, Phase6VerificationReport):
        return _fail("Phase 6 post-change verification evidence is missing", target=normalized_target, artifact_digest=actual_artifact)
    if post_change_report.request_digest != expected_request:
        return _fail(
            "post-change verification belongs to a different request",
            target=normalized_target,
            artifact_digest=actual_artifact,
            verification_digest=post_change_report.evidence_digest,
        )
    if not post_change_report.passed:
        return _fail(
            "post-change verification has not passed",
            target=normalized_target,
            artifact_digest=actual_artifact,
            verification_digest=post_change_report.evidence_digest,
        )
    if review_policy is None or not isinstance(review_policy, ReviewPolicyResult):
        return _fail(
            "review-policy evidence is missing",
            target=normalized_target,
            artifact_digest=actual_artifact,
            verification_digest=post_change_report.evidence_digest,
        )
    if not review_policy.allowed:
        return _fail(
            "review policy gate has not passed",
            target=normalized_target,
            artifact_digest=actual_artifact,
            verification_digest=post_change_report.evidence_digest,
        )

    return ReleaseBoundaryResult(
        True,
        "release is eligible for controlled non-production execution; deployment remains a separate capability",
        normalized_target,
        actual_artifact,
        post_change_report.evidence_digest,
        False,
    )
