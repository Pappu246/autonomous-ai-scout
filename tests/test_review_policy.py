from autonomous_agent.review_policy import evaluate_review_policy


def passing(**overrides):
    values = {
        "pr_state": "open",
        "ci_passed": True,
        "post_change_verified": True,
        "approvals": 1,
        "required_approvals": 1,
        "unresolved_threads": 0,
        "mergeable": True,
    }
    values.update(overrides)
    return evaluate_review_policy(**values)


def test_policy_passes_only_when_all_gates_pass():
    result = passing()
    assert result.allowed
    assert "separate operation" in result.reason


def test_policy_requires_human_approval():
    result = passing(approvals=0)
    assert not result.allowed
    assert "approvals" in result.reason


def test_policy_blocks_unresolved_threads():
    result = passing(unresolved_threads=1)
    assert not result.allowed
    assert "unresolved" in result.reason


def test_policy_blocks_failed_ci():
    result = passing(ci_passed=False)
    assert not result.allowed
    assert "CI" in result.reason


def test_policy_blocks_failed_post_change_verification():
    result = passing(post_change_verified=False)
    assert not result.allowed
    assert "verification" in result.reason


def test_policy_blocks_non_reviewable_pr():
    result = passing(pr_state="closed")
    assert not result.allowed
    assert "reviewable" in result.reason


def test_policy_blocks_unmergeable_pr():
    result = passing(mergeable=False)
    assert not result.allowed
    assert "mergeable" in result.reason


def test_policy_fails_closed_for_invalid_inputs():
    result = evaluate_review_policy(
        pr_state="open",
        ci_passed=True,
        post_change_verified=True,
        approvals="not-a-number",
        required_approvals=1,
        unresolved_threads=0,
        mergeable=True,
    )
    assert not result.allowed
    assert "invalid" in result.reason


def test_policy_requires_positive_approval_threshold():
    result = passing(required_approvals=0)
    assert not result.allowed
    assert "at least one" in result.reason
