from __future__ import annotations

from autonomous_agent.github_api import GitHubApiClient, GitHubApiConfig, GitHubApiError


class FakeApi:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, method, url, *, headers, params=None, json=None):
        self.calls.append((method, url, dict(headers), params, json))
        if not self.responses:
            raise AssertionError("unexpected API call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(fake):
    return GitHubApiClient(
        GitHubApiConfig(base_url="https://api.github.test"),
        http_request=fake,
    )


def test_create_branch_uses_verified_base_head(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [
        {"commit": {"sha": "a" * 40}},
        {"commit": {"sha": "a" * 40}},
        {"ref": "refs/heads/improvement/one"},
    ]

    result = client(fake).create_branch(
        "owner/repo",
        "improvement/one",
        "main",
    )

    assert result == "refs/heads/improvement/one"
    assert fake.calls[0][0:2] == (
        "GET",
        "https://api.github.test/repos/owner/repo/branches/main",
    )
    assert [call[0] for call in fake.calls] == ["GET", "GET", "POST"]
    assert fake.calls[2][3] is None
    assert fake.calls[2][4]["sha"] == "a" * 40
    assert fake.calls[0][2]["Authorization"] == "Bearer secret"


def test_commit_files_creates_one_git_commit(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [
        {"commit": {"sha": "b" * 40}},
        {"tree": {"sha": "c" * 40}},
        {"sha": "d" * 40},
        {"sha": "e" * 40},
        {"sha": "f" * 40},
        {"sha": "g" * 40},
        {},
    ]

    result = client(fake).commit_files(
        "owner/repo",
        "improvement/one",
        {"app.py": "print(1)\n", "tests/test_app.py": "assert True\n"},
        "chore: prepare approved change",
    )

    assert result == "g" * 40
    methods = [call[0] for call in fake.calls]
    assert methods == ["GET", "GET", "POST", "POST", "POST", "POST", "PATCH"]
    tree_payload = fake.calls[4][4]
    assert tree_payload["base_tree"] == "c" * 40
    assert len(tree_payload["tree"]) == 2
    assert fake.calls[-1][4] == {"sha": "g" * 40, "force": False}


def test_open_draft_pr_sets_draft(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [{"html_url": "https://github.com/owner/repo/pull/7"}]

    result = client(fake).open_draft_pr(
        "owner/repo",
        "improvement/one",
        "main",
        "Fix",
        "Review this change.",
    )

    assert result.endswith("/pull/7")
    payload = fake.calls[0][4]
    assert payload["draft"] is True
    assert payload["head"] == "improvement/one"
    assert payload["base"] == "main"


def test_status_maps_failed_check_run_to_failure(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [
        {"head": {"sha": "h" * 40}},
        {"check_runs": [
            {"name": "CI", "status": "completed", "conclusion": "failure"}
        ]},
    ]

    result = client(fake).status("owner/repo", "#7")
    assert result == "failure"


def test_status_uses_check_runs_when_legacy_status_is_empty(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [
        {"head": {"sha": "h" * 40}},
        {"check_runs": [
            {"name": "CI", "status": "completed", "conclusion": "success"}
        ]},
    ]

    result = client(fake).status("owner/repo", "#7")

    assert result == "success"
    assert fake.calls[1][1].endswith("/commits/" + ("h" * 40) + "/check-runs")


def test_missing_token_fails_closed(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    fake = FakeApi()
    api = client(fake)

    try:
        api.open_draft_pr("owner/repo", "x", "main", "title", "body")
    except GitHubApiError as exc:
        assert "GITHUB_TOKEN" in str(exc)
    else:
        raise AssertionError("missing token must fail closed")


def test_unsafe_file_path_fails_before_api(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    api = client(fake)

    try:
        api.commit_files("owner/repo", "x", {"../outside.py": "x"}, "bad")
    except GitHubApiError as exc:
        assert "file path" in str(exc)
    else:
        raise AssertionError("unsafe path must fail closed")
    assert fake.calls == []


def test_find_existing_pull_request_is_conservative(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [{"items": [{"html_url": "https://github.com/owner/repo/pull/9"}]}]

    result = client(fake).find(
        "owner/repo",
        "improvement/one",
        "main",
        "digest",
    )

    assert result.endswith("/pull/9")
    assert fake.calls[0][3]["head"] == "owner:improvement/one"
    assert fake.calls[0][3]["base"] == "main"

def test_find_accepts_real_github_pull_list_response(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [[{"html_url": "https://github.com/owner/repo/pull/10"}]]

    result = client(fake).find("owner/repo", "improvement/one", "main", "digest")

    assert result.endswith("/pull/10")


def test_get_accepts_pull_request_url(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [{"number": 7, "state": "open", "head": {"sha": "a" * 40}}]

    result = client(fake).get("owner/repo", "https://github.com/owner/repo/pull/7")

    assert result["number"] == 7
    assert fake.calls[0][1].endswith("/pulls/7")

def test_create_branch_at_sha_rejects_stale_remote_head(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    fake = FakeApi()
    fake.responses = [{"commit": {"sha": "b" * 40}}]

    try:
        client(fake).create_branch_at_sha(
            "owner/repo",
            "improvement/one",
            "main",
            "a" * 40,
        )
    except GitHubApiError as exc:
        assert "changed before branch creation" in str(exc)
    else:
        raise AssertionError("stale base HEAD must be rejected")
    assert [call[0] for call in fake.calls] == ["GET"]
