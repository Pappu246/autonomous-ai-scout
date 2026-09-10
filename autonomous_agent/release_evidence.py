from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class ReleaseEvidence:
    artifact_digest: str
    target: str
    commit_sha: str
    pull_request: int
    ci_passed: bool
    post_change_verified: bool
    review_policy_passed: bool
    release_approved: bool
    evidence_digest: str


class ReleaseEvidenceError(ValueError):
    """Raised when release evidence is malformed or incomplete."""


def _normalized_sha(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ReleaseEvidenceError(f"{field} must be a string")
    value = value.strip().lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise ReleaseEvidenceError(f"{field} must be a 40-character hex SHA")
    return value


def build_release_evidence(
    *,
    artifact_digest: str,
    target: str,
    commit_sha: str,
    pull_request: int,
    ci_passed: bool,
    post_change_verified: bool,
    review_policy_passed: bool,
    release_approved: bool,
) -> ReleaseEvidence:
    """Create immutable, deterministic evidence for a release decision.

    This function is decision/audit data only. It does not deploy, merge, mutate
    infrastructure, read secrets, or grant any capability.
    """
    try:
        digest = str(artifact_digest).strip().lower()
        normalized_target = str(target).strip().lower()
        pr = int(pull_request)
    except (TypeError, ValueError):
        raise ReleaseEvidenceError("release evidence inputs are malformed") from None

    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ReleaseEvidenceError("artifact_digest must be a 64-character hex SHA-256 digest")
    if not normalized_target:
        raise ReleaseEvidenceError("target must not be blank")
    if pr < 1:
        raise ReleaseEvidenceError("pull_request must be positive")

    sha = _normalized_sha(commit_sha, "commit_sha")
    payload = {
        "artifact_digest": digest,
        "target": normalized_target,
        "commit_sha": sha,
        "pull_request": pr,
        "ci_passed": bool(ci_passed),
        "post_change_verified": bool(post_change_verified),
        "review_policy_passed": bool(review_policy_passed),
        "release_approved": bool(release_approved),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    evidence_digest = hashlib.sha256(canonical).hexdigest()
    return ReleaseEvidence(**payload, evidence_digest=evidence_digest)


def verify_release_evidence(
    evidence: ReleaseEvidence,
    *,
    expected_artifact_digest: str,
    expected_target: str,
    expected_commit_sha: str,
    expected_pull_request: int,
) -> bool:
    """Verify that previously recorded evidence still binds to exact release inputs."""
    try:
        expected_digest = str(expected_artifact_digest).strip().lower()
        expected_target_norm = str(expected_target).strip().lower()
        expected_sha = _normalized_sha(expected_commit_sha, "expected_commit_sha")
        expected_pr = int(expected_pull_request)
    except (TypeError, ValueError, ReleaseEvidenceError):
        return False

    if not isinstance(evidence, ReleaseEvidence):
        return False
    try:
        recreated = build_release_evidence(
            artifact_digest=evidence.artifact_digest,
            target=evidence.target,
            commit_sha=evidence.commit_sha,
            pull_request=evidence.pull_request,
            ci_passed=evidence.ci_passed,
            post_change_verified=evidence.post_change_verified,
            review_policy_passed=evidence.review_policy_passed,
            release_approved=evidence.release_approved,
        )
    except ReleaseEvidenceError:
        return False

    return (
        recreated.evidence_digest == evidence.evidence_digest
        and evidence.artifact_digest == expected_digest
        and evidence.target == expected_target_norm
        and evidence.commit_sha == expected_sha
        and evidence.pull_request == expected_pr
    )
