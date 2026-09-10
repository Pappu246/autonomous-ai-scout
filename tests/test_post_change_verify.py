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
        "Pappu246/autonomous-ai-scout", "agent/change-1", "commit-sha", "pr",
        {"app.py": "print('new')\n"}, {"app.py": "print('new')\n"}, FakeBackend()
    )
    assert result.passed
    assert [c.name for c in result.checks] == [
        "commit_identity", "pull_request_state", "file_snapshot", "post_change_tests"
    ]


def test_commit_drift_blocks():
    result = verify_post_change("repo", "agent/change", "expected", "pr", {"a": "x"}, {"a": "x"}, FakeBackend(head="different"))
    assert not result.passed
    assert not result.checks[0].passed


def test_file_drift_blocks():
    result = verify_post_change("repo", "agent/change", "commit-sha", "pr", {"a": "expected"}, {"a": "tampered"}, FakeBackend())
    assert not result.passed
    assert not result.checks[2].passed


def test_test_failure_blocks():
    result = verify_post_change("repo", "agent/change", "commit-sha", "pr", {"a": "x"}, {"a": "x"}, FakeBackend(tests_ok=False))
    assert not result.passed
    assert not result.checks[3].passed


def test_closed_pr_blocks():
    result = verify_post_change("repo", "agent/change", "commit-sha", "pr", {"a": "x"}, {"a": "x"}, FakeBackend(state="closed"))
    assert not result.passed
    assert not result.checks[1].passed
