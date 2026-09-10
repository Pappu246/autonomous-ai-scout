from __future__ import annotations

import hashlib
import pytest

from autonomous_agent.release_evidence import (
    ReleaseEvidenceError,
    build_release_evidence,
    verify_release_evidence,
)


ARTIFACT = hashlib.sha256(b"release-artifact").hexdigest()
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def make(**overrides):
    values = dict(
        artifact_digest=ARTIFACT,
        target="staging",
        commit_sha=COMMIT,
        pull_request=50,
        ci_passed=True,
        post_change_verified=True,
        review_policy_passed=True,
        release_approved=True,
    )
    values.update(overrides)
    return values


def test_builds_deterministic_evidence_and_verifies():
    evidence = build_release_evidence(**make())
    assert len(evidence.evidence_digest) == 64
    assert verify_release_evidence(
        evidence,
        expected_artifact_digest=ARTIFACT,
        expected_target="staging",
        expected_commit_sha=COMMIT,
        expected_pull_request=50,
    )
    assert evidence == build_release_evidence(**make())


def test_artifact_drift_fails():
    evidence = build_release_evidence(**make())
    other = hashlib.sha256(b"other").hexdigest()
    assert not verify_release_evidence(
        evidence,
        expected_artifact_digest=other,
        expected_target="staging",
        expected_commit_sha=COMMIT,
        expected_pull_request=50,
    )


def test_tampered_evidence_digest_fails():
    evidence = build_release_evidence(**make())
    tampered = evidence.__class__(**{**evidence.__dict__, "evidence_digest": "0" * 64})
    assert not verify_release_evidence(
        tampered,
        expected_artifact_digest=ARTIFACT,
        expected_target="staging",
        expected_commit_sha=COMMIT,
        expected_pull_request=50,
    )


def test_target_or_commit_or_pr_drift_fails():
    evidence = build_release_evidence(**make())
    assert not verify_release_evidence(evidence, expected_artifact_digest=ARTIFACT, expected_target="sandbox", expected_commit_sha=COMMIT, expected_pull_request=50)
    assert not verify_release_evidence(evidence, expected_artifact_digest=ARTIFACT, expected_target="staging", expected_commit_sha="f" * 40, expected_pull_request=50)
    assert not verify_release_evidence(evidence, expected_artifact_digest=ARTIFACT, expected_target="staging", expected_commit_sha=COMMIT, expected_pull_request=51)


@pytest.mark.parametrize(
    "overrides",
    [
        {"artifact_digest": ""},
        {"artifact_digest": "z" * 64},
        {"target": "  "},
        {"commit_sha": "bad"},
        {"commit_sha": "z" * 40},
        {"pull_request": 0},
    ],
)
def test_malformed_input_fails_closed(overrides):
    with pytest.raises(ReleaseEvidenceError):
        build_release_evidence(**make(**overrides))


def test_verification_malformed_expected_input_fails_closed():
    evidence = build_release_evidence(**make())
    assert not verify_release_evidence(
        evidence,
        expected_artifact_digest="bad",
        expected_target="staging",
        expected_commit_sha=COMMIT,
        expected_pull_request=50,
    )
