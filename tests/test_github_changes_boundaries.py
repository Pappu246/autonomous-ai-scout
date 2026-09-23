from datetime import datetime, timedelta, timezone

import pytest

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


def action(task="inspect application code"):
    return PendingAction(
        "boundary-action",
        task,
        ("inspect application",),
        "low",
        "validated maintenance improvement",
        "approved",
    )


def test_build_rejects_forbidden_capability_terms():
    with pytest.raises(ValueError, match="forbidden capability"):
        build_change_request(
            action("deploy application changes"),
            "Pappu246/autonomous-ai-scout",
            "main",
            "agent/safe-change",
            "Safe inspection",
            "Prepared for review.",
            DIFF,
        )


def test_build_rejects_protected_head_branches():
    with pytest.raises(ValueError, match="non-protected branch"):
        build_change_request(
            action(),
            "Pappu246/autonomous-ai-scout",
            "main",
            "production",
            "Safe inspection",
            "Prepared for review.",
            DIFF,
        )


def test_execute_rejects_file_manifest_mismatch_before_remote_calls(tmp_path):
    class Backend:
        def __init__(self):
            self.calls = 0

        def create_branch(self, *args):
            self.calls += 1
            return "branch"

        def commit_files(self, *args):
            self.calls += 1
            return "commit"

        def open_draft_pr(self, *args):
            self.calls += 1
            return "pr"

    act = action()
    approval = ApprovalRecord.for_action(
        act, "single-use", datetime.now(timezone.utc), timedelta(hours=1)
    )
    request = build_change_request(
        act,
        "Pappu246/autonomous-ai-scout",
        "main",
        "agent/safe-change",
        "Safe inspection",
        "Prepared for review.",
        DIFF,
    )
    backend = Backend()
    result = execute_approved_change(
        act,
        approval,
        request,
        DIFF,
        {"different.py": "print('new')\n"},
        tmp_path / "claims",
        backend,
    )
    assert not result.allowed
    assert "manifest" in result.reason
    assert backend.calls == 0


def test_execute_rejects_file_contents_not_produced_by_verified_base(tmp_path):
    class Backend:
        def __init__(self):
            self.calls = 0
        def read_file_at_ref(self, repository, path, ref):
            return "print('old')\n"
        def create_branch(self, *args):
            self.calls += 1
            return "branch"
        def commit_files(self, *args, **kwargs):
            self.calls += 1
            return "commit"
        def open_draft_pr(self, *args):
            self.calls += 1
            return "pr"

    act = action()
    approval = ApprovalRecord.for_action(
        act, "single-use", datetime.now(timezone.utc), timedelta(hours=1)
    )
    request = build_change_request(
        act,
        "Pappu246/autonomous-ai-scout",
        "main",
        "agent/safe-change",
        "Safe inspection",
        "Prepared for review.",
        DIFF,
    )
    backend = Backend()
    result = execute_approved_change(
        act,
        approval,
        request,
        DIFF,
        {"app.py": "print('new')\nprint('attacker')\n"},
        tmp_path / "claims",
        backend,
    )
    assert not result.allowed
    assert "supplied file contents" in result.reason
    assert backend.calls == 0


def test_execute_binds_commit_to_expected_parent_and_fails_closed_on_race(tmp_path):
    class Backend:
        def __init__(self):
            self.calls = []
        def read_file_at_ref(self, repository, path, ref):
            return "print('old')\n"
        def create_branch(self, *args):
            self.calls.append(("branch", args))
            return "branch"
        def commit_files(self, *args, **kwargs):
            self.calls.append(("commit", args, kwargs))
            assert kwargs["expected_parent_sha"] == "b" * 40
            raise RuntimeError("branch parent changed")
        def open_draft_pr(self, *args):
            self.calls.append(("pr", args))
            return "pr"

    act = action()
    approval = ApprovalRecord.for_action(
        act, "single-use", datetime.now(timezone.utc), timedelta(hours=1)
    )
    request = build_change_request(
        act,
        "Pappu246/autonomous-ai-scout",
        "main",
        "agent/safe-change",
        "Safe inspection",
        "Prepared for review.",
        DIFF,
        expected_head_sha="b" * 40,
    )
    backend = Backend()
    result = execute_approved_change(
        act,
        approval,
        request,
        DIFF,
        {"app.py": "print('new')\n"},
        tmp_path / "claims",
        backend,
    )
    assert not result.allowed
    assert "failed closed" in result.reason
    assert [entry[0] for entry in backend.calls] == ["branch", "commit"]

