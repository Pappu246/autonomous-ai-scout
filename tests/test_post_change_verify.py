from autonomous_agent.post_change_verify import verify_post_change


class FakeBackend:
    def __init__(self, head="commit-sha", state="draft", tests_ok=True):
        self.head = head
        self.state = state
        self.tests_ok = tests_ok

    def get_head_commit(self, repository, branch):
        return self.head

    def get_pr_state(self, repository, pull_request):
        return self.state

    def run_tests(self, repository, branch):
        return self.tests_ok, "tests passed" if self.tests_ok else "tests failed"


def test_post_change_verification_passes():
    result = verify_post_change(
        "Pappu246/autonomous-ai-scout",
        "agent/change-1",
        "commit-sha",
        "https://github.com/Pappu246/autonomous-ai-scout/pull/1",
        {"app.py": "print('new')\n"},
        {"app.py": "print('new')\n"},
        FakeBackend(),
    )
    assert result.passed
    assert len(result.checks) == 4


def test_post_change_verification_blocks_on_commit_drift():
    result = verify_post_change(
        "repo",
        "agent/change-1",
        "expected",
        "pr",
        {"app.py": "new"},
        {"app.py": "new"},
        FakeBackend(head="different"),
    )
    assert not result.passed
    assert result.checks[0].name == "commit_identity"
    assert not result.checks[0].passed


def test_post_change_verification_blocks_on_file_drift():
    result = verify_post_change(
        "repo",
        "agent/change-1",
        "commit-sha",
        "pr",
        {"app.py": "expected"},
        {"app.py": "tampered"},
        FakeBackend(),
    )
    assert not result.passed
    assert not result.checks[2].passed


def test_post_change_verification_blocks_on_test_failure():
    result = verify_post_change(
        "repo",
        "agent/change-1",
        "commit-sha",
        "pr",
        {"app.py": "expected"},
        {"app.py": "expected"},
        FakeBackend(tests_ok=False),
    )
    assert not result.passed
    assert not result.checks[3].passed


def test_post_change_verification_rejects_closed_pr():
    result = verify_post_change(
        "repo",
        "agent/change-1",
        "commit-sha",
        "pr",
        {"app.py": "expected"},
        {"app.py": "expected"},
        FakeBackend(state="closed"),
    )
    assert not result.passed
    assert not result.checks[1].passed
