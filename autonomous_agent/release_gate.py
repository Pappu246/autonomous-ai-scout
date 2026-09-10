from __future__ import annotations

from dataclasses import dataclass


SAFE_RELEASE_TARGETS = frozenset({"sandbox", "staging"})
FORBIDDEN_RELEASE_TARGETS = frozenset({"production", "prod"})


@dataclass(frozen=True)
class ReleaseGateResult:
    allowed: bool
    reason: str
    target: str
    deployment_permitted: bool


def evaluate_release_gate(
    *,
    artifact_digest: str,
    expected_artifact_digest: str,
    target: str,
    ci_passed: bool,
    post_change_verified: bool,
    review_policy_passed: bool,
    release_approved: bool,
    allowed_targets: frozenset[str] = SAFE_RELEASE_TARGETS,
) -> ReleaseGateResult:
    """Decide whether a release is eligible for a controlled non-production target.

    This function is intentionally decision-only. It never deploys, mutates infrastructure,
    accesses secrets, changes billing, or grants production-release capability.
    Production targets are always rejected and the default allowlist is sandbox/staging.
    Missing or malformed inputs fail closed.
    """
    try:
        normalized_target = target.strip().lower()
        actual_digest = artifact_digest.strip().lower()
        expected_digest = expected_artifact_digest.strip().lower()
        normalized_allowed = frozenset(value.strip().lower() for value in allowed_targets)
    except (AttributeError, TypeError):
        return ReleaseGateResult(False, "release gate inputs are invalid", "", False)

    if not normalized_target:
        return ReleaseGateResult(False, "release target is required", normalized_target, False)
    if normalized_target in FORBIDDEN_RELEASE_TARGETS:
        return ReleaseGateResult(False, "production release is outside the autonomous boundary", normalized_target, False)
    if not actual_digest or not expected_digest:
        return ReleaseGateResult(False, "artifact digest is required", normalized_target, False)
    if actual_digest != expected_digest:
        return ReleaseGateResult(False, "release artifact digest does not match", normalized_target, False)
    if normalized_target not in normalized_allowed:
        return ReleaseGateResult(False, "release target is not allowlisted", normalized_target, False)
    if not ci_passed:
        return ReleaseGateResult(False, "required CI checks have not passed", normalized_target, False)
    if not post_change_verified:
        return ReleaseGateResult(False, "post-change verification has not passed", normalized_target, False)
    if not review_policy_passed:
        return ReleaseGateResult(False, "review policy gate has not passed", normalized_target, False)
    if not release_approved:
        return ReleaseGateResult(False, "explicit release approval is missing", normalized_target, False)

    return ReleaseGateResult(
        True,
        "release is eligible for controlled non-production execution; deployment remains a separate capability",
        normalized_target,
        False,
    )
