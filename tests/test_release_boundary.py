from __future__ import annotations

import hashlib

from autonomous_agent.post_change_final import (
    CheckConclusion,
    CheckObservation,
    Phase6Evidence,
    TestAttestation,
    TestAttestationPolicy,
    verify_phase6,
)
from autonomous_agent.post_change_evidence import CommitObservation, PullRequestObservation
from autonomous_agent.post_change_snapshot import SnapshotEntry, SnapshotFileStatus, SnapshotObservation
from autonomous_agent.post_change_verification import (
    FileManifestEntry,
    PostChangeTestEvidence,
    PullRequestEvidence,
    TestPolicy,
    VerificationRequest,
)
from autonomous_agent.release_boundary import evaluate_release_boundary
from autonomous_agent.review_policy import ReviewPolicyResult


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SHA = "a" * 40
ARTIFACT = _digest("artifact")
REQUEST = VerificationRequest(
    repository="owner/repo",
    pull_request_number=12,
    expected_head_branch="feature/change",
    expected_base_branch="main",
    expected_commit_sha=SHA,
    expected_files=(FileManifestEntry("README.md", _digest("readme")),),
    test_policy=TestPolicy(checks=({"name": "CI", "required": True},)),
)


def _verified_report():
    commit = CommitObservation("owner/repo", SHA, "feature/change", "main", True)
    pr = PullRequestObservation("owner/repo", 12, "open", False, False, "owner/repo", "feature/change", "main", SHA, True)
    snapshot = SnapshotObservation(
        "owner/repo",
        SHA,
        (SnapshotEntry("README.md", _digest("readme"), SnapshotFileStatus.MODIFIED, 6, False, False, False, None),),
        complete=True,
        truncated=False,
        observed_file_count=1,
    )
    tests = TestAttestation(
        repository="owner/repo",
        commit_sha=SHA,
        checks=(
            CheckObservation(
                name="CI",
                repository="owner/repo",
                head_sha=SHA,
                event="pull_request",
                status="completed",
                conclusion=CheckConclusion.SUCCESS,
                run_id=101,
                completed=True,
                workflow_name="CI",
            ),
        ),
        complete=True,
    )
    evidence = Phase6Evidence(commit=commit, pull_request=pr, snapshot=snapshot, tests=tests)
    return verify_phase6(REQUEST, evidence, test_policy=TestAttestationPolicy())


REVIEW = ReviewPolicyResult(
    allowed=True,
    reason="review policy passed",
    required_approvals=1,
    approvals=1,
    unresolved_threads=0,
)


BASE = {
    "artifact_digest": ARTIFACT,
    "expected_artifact_digest": ARTIFACT,
    "target": "staging",
    "ci_passed": True,
    "post_change_report": _verified_report(),
    "expected_request_digest": REQUEST.canonical() and __import__("hashlib").sha256(
        __import__("json").dumps(REQUEST.canonical(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest(),
    "review_policy": REVIEW,
    "release_approved": True,
}


def test_release_boundary_passes_only_for_verified_staging():
    result = evaluate_release_boundary(**BASE)
    assert result.allowed is True
    assert result.target == "staging"
    assert result.deployment_permitted is False


def test_release_boundary_rejects_production():
    result = evaluate_release_boundary(**{**BASE, "target": "production"})
    assert result.allowed is False
    assert "production" in result.reason


def test_release_boundary_rejects_artifact_drift():
    result = evaluate_release_boundary(**{**BASE, "artifact_digest": _digest("different")})
    assert result.allowed is False
    assert "artifact digest" in result.reason


def test_release_boundary_rejects_wrong_request_binding():
    result = evaluate_release_boundary(**{**BASE, "expected_request_digest": _digest("other-request")})
    assert result.allowed is False
    assert "different request" in result.reason


def test_release_boundary_rejects_unverified_post_change():
    report = _verified_report()
    report = type(report)(
        verdict=type(report.verdict).BLOCKED,
        stages=report.stages,
        request_digest=report.request_digest,
        evidence_digest=report.evidence_digest,
        policy_digest=report.policy_digest,
        summary="blocked",
    )
    result = evaluate_release_boundary(**{**BASE, "post_change_report": report})
    assert result.allowed is False
    assert "post-change verification" in result.reason


def test_release_boundary_rejects_review_policy_failure():
    failed = ReviewPolicyResult(False, "missing approval", 1, 0, 0)
    result = evaluate_release_boundary(**{**BASE, "review_policy": failed})
    assert result.allowed is False
    assert "review policy" in result.reason


def test_release_boundary_requires_explicit_approval():
    result = evaluate_release_boundary(**{**BASE, "release_approved": False})
    assert result.allowed is False
    assert "release approval" in result.reason


def test_release_boundary_rejects_missing_verification():
    result = evaluate_release_boundary(**{**BASE, "post_change_report": None})
    assert result.allowed is False
    assert "verification evidence" in result.reason


def test_release_boundary_never_grants_deployment():
    result = evaluate_release_boundary(**BASE)
    assert result.deployment_permitted is False


def test_release_boundary_allowlist_is_enforced():
    result = evaluate_release_boundary(**{**BASE, "target": "qa"})
    assert result.allowed is False
    assert "allowlisted" in result.reason


def test_release_boundary_fails_closed_on_invalid_digest():
    result = evaluate_release_boundary(**{**BASE, "artifact_digest": "abc"})
    assert result.allowed is False
    assert "SHA-256" in result.reason
