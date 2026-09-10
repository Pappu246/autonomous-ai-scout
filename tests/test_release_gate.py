from autonomous_agent.release_gate import evaluate_release_gate


BASE = {
    "artifact_digest": "abc123",
    "expected_artifact_digest": "abc123",
    "target": "staging",
    "ci_passed": True,
    "post_change_verified": True,
    "review_policy_passed": True,
    "release_approved": True,
}


def test_release_gate_passes_for_approved_staging_release():
    result = evaluate_release_gate(**BASE)

    assert result.allowed is True
    assert result.target == "staging"
    assert result.deployment_permitted is False


def test_release_gate_rejects_digest_drift():
    result = evaluate_release_gate(**{**BASE, "artifact_digest": "different"})

    assert result.allowed is False
    assert "digest" in result.reason
    assert result.deployment_permitted is False


def test_release_gate_rejects_production_even_when_everything_else_passes():
    result = evaluate_release_gate(**{**BASE, "target": "production"})

    assert result.allowed is False
    assert "production" in result.reason
    assert result.deployment_permitted is False


def test_release_gate_rejects_unallowlisted_target():
    result = evaluate_release_gate(**{**BASE, "target": "qa"})

    assert result.allowed is False
    assert "allowlisted" in result.reason


def test_release_gate_requires_all_prerequisites():
    for field in ("ci_passed", "post_change_verified", "review_policy_passed", "release_approved"):
        result = evaluate_release_gate(**{**BASE, field: False})
        assert result.allowed is False
        assert result.deployment_permitted is False


def test_release_gate_rejects_blank_digest():
    result = evaluate_release_gate(**{**BASE, "artifact_digest": "   "})

    assert result.allowed is False
    assert "digest" in result.reason


def test_release_gate_fails_closed_on_malformed_inputs():
    result = evaluate_release_gate(
        artifact_digest=None,
        expected_artifact_digest="abc123",
        target="staging",
        ci_passed=True,
        post_change_verified=True,
        review_policy_passed=True,
        release_approved=True,
    )

    assert result.allowed is False
    assert result.deployment_permitted is False
