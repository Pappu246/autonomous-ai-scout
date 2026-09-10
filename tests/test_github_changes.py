from datetime import datetime, timedelta, timezone

from autonomous_agent.action_queue import PendingAction
from autonomous_agent.approved_executor import ApprovalRecord
from autonomous_agent.github_changes import build_change_request, execute_approved_change


DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print('old')
+print('new')
"""


class FakeBackend:
    def __init__(self):
        self.calls = []

    def create_branch(self, repository, branch, base_branch):
        self.calls.append(("branch", repository, branch, base_branch))
        return branch

    def commit_files(self, repository, branch, files, message):
        self.calls.append(("commit", repository, branch, dict(files), message))
        return "commit-sha"

    def open_draft_pr(self, repository, head_branch, base_branch, title, body):
        self.calls.append(("pr", repository, head_branch, base_branch, title, body))
        return "https://github.com/Pappu246/autonomous-ai-scout/pull/999"


def approved_action():
    return PendingAction(
        "action-5",
        "inspect application code",
        ("inspect application", "test application"),
        "low",
        "validated maintenance improvement",
        "approved",
    )


def approval_for(action):
    now = datetime.now(timezone.utc)
    return ApprovalRecord.for_action(action, "single-use-approval", now, timedelta(hours=1))


def test_build_change_request_is_metadata_only():
    action = approved_action()
    request = build_change_request(
        action,
        "Pappu246/autonomous-ai-scout",
        "main",
        "agent/change-action-5",
        "Improve safe inspection",
        "Prepared for review only.",
        DIFF,
    )
    assert request.requires_approval
    assert request.base_branch == "main"
    assert request.head_branch == "agent/change-action-5"
    assert request.files == ("app.py",)
    assert request.additions == 1
    assert request.deletions == 1


def test_execute_rejects_mismatched_patch_digest(tmp_path):
    action = approved_action()
    approval = approval_for(action)
    request = build_change_request(
        action,
        "Pappu246/autonomous-ai-scout",
        "main",
        "agent/change-action-5",
        "Improve safe inspection",
        "Prepared for review only.",
        DIFF,
    )
    changed = DIFF.replace("new", "changed")
    result = execute_approved_change(
        action,
        approval,
        request,
        changed,
        {"app.py": "print('changed')\n"},
        tmp_path / "claims",
        FakeBackend(),
    )
    assert not result.allowed
    assert "digest" in result.reason


def test_execute_requires_approval_and_claims_once(tmp_path):
    action = approved_action()
    approval = approval_for(action)
    request = build_change_request(
        action,
        "Pappu246/autonomous-ai-scout",
        "main",
        "agent/change-action-5",
        "Improve safe inspection",
        "Prepared for review only.",
        DIFF,
    )
    backend = FakeBackend()
    result = execute_approved_change(
        action,
        approval,
        request,
        DIFF,
        {"app.py": "print('new')\n"},
        tmp_path / "claims",
        backend,
    )
    assert result.allowed
    assert result.pull_request.endswith("/999")
    assert [call[0] for call in backend.calls] == ["branch", "commit", "pr"]

    replay = execute_approved_change(
        action,
        approval,
        request,
        DIFF,
        {"app.py": "print('new')\n"},
        tmp_path / "claims",
        backend,
    )
    assert not replay.allowed
    assert "already been consumed" in replay.reason
    assert len(backend.calls) == 3


def test_execute_rejects_protected_head_branch(tmp_path):
    action = approved_action()
    approval = approval_for(action)
    request = build_change_request(
        action,
        "Pappu246/autonomous-ai-scout",
        "main",
        "agent/change-action-5",
        "Improve safe inspection",
        "Prepared for review only.",
        DIFF,
    )
    protected = type(request)(
        **{**request.__dict__, "head_branch": "main"}
    )
    result = execute_approved_change(
        action,
        approval,
        protected,
        DIFF,
        {"app.py": "print('new')\n"},
        tmp_path / "claims",
        FakeBackend(),
    )
    assert not result.allowed
    assert "protected" in result.reason
