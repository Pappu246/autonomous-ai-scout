from __future__ import annotations

import pytest

from autonomous_agent.continuous_improvement import build_proposal
from autonomous_agent.improvement_actions import proposal_to_action
from autonomous_agent.improvement_github import build_improvement_change_request
from autonomous_agent.models import ProjectFinding


def proposal():
    return build_proposal(
        "owner/repo",
        ProjectFinding(
            repository="owner/repo",
            severity="high",
            title="Open regression",
            detail="CI failure detected",
            recommendation="Add a regression test",
            confidence=0.9,
        ),
    )


def test_build_change_request_is_read_only_metadata_only():
    item = proposal()
    action = proposal_to_action(item)
    diff = "--- a/tests/test_regression.py\n+++ b/tests/test_regression.py\n@@ -1 +1 @@\n-old\n+new\n"
    request = build_improvement_change_request(item, action, base_branch="main", head_branch="improvement/regression", unified_diff=diff)
    assert request.repository == "owner/repo"
    assert request.base_branch == "main"
    assert request.head_branch == "improvement/regression"
    assert request.requires_approval is True
    assert request.patch_digest
    assert "protected-branch operations" in request.body


def test_protected_head_branch_is_rejected():
    item = proposal()
    action = proposal_to_action(item)
    with pytest.raises(ValueError, match="head branch"):
        build_improvement_change_request(item, action, base_branch="main", head_branch="main", unified_diff="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n")
