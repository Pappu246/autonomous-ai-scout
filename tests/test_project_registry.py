from __future__ import annotations

from autonomous_agent.project_registry import build_project_registry, discover_repositories


def _repo(name: str, *, archived: bool = False, fingerprint_seed: str = "a") -> dict:
    return {
        "id": abs(hash(name)) % 1_000_000,
        "name": name.rsplit("/", 1)[-1],
        "full_name": name,
        "owner": {"login": "Pappu246"},
        "visibility": "public",
        "private": False,
        "archived": archived,
        "fork": False,
        "default_branch": "main",
        "language": "Python",
        "description": fingerprint_seed,
        "updated_at": f"2026-09-10T00:00:0{len(fingerprint_seed)}Z",
        "pushed_at": "2026-09-10T00:00:00Z",
        "open_issues_count": 0,
        "html_url": f"https://github.com/{name}",
    }


def test_discover_repositories_paginates_and_keeps_archived_and_forks(monkeypatch):
    calls = []
    first = [_repo(f"Pappu246/project-{i}") for i in range(100)]
    first[-1]["fork"] = True
    second = [_repo("Pappu246/archived-project", archived=True)]

    def fake_get(path, params=None):
        calls.append((path, params))
        return first if params["page"] == 1 else second if params["page"] == 2 else []

    monkeypatch.setattr("autonomous_agent.project_registry.gh_get", fake_get)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    repos = discover_repositories("Pappu246")

    assert repos is not None
    assert len(repos) == 101
    assert any(item["fork"] for item in repos)
    assert any(item["archived"] for item in repos)
    assert calls[0][0] == "/users/Pappu246/repos"
    assert calls[0][1]["page"] == 1
    assert calls[1][1]["page"] == 2


def test_discovery_failure_is_distinguished_from_empty_account(monkeypatch):
    monkeypatch.setattr("autonomous_agent.project_registry.gh_get", lambda path, params=None: None)
    assert discover_repositories("Pappu246") is None

    previous = {"Pappu246/existing": {"full_name": "Pappu246/existing", "fingerprint": "old"}}
    projects, changes = build_project_registry("Pappu246", previous)
    assert projects == previous
    assert changes == {"new": [], "changed": [], "removed": []}


def test_authenticated_discovery_uses_owner_affiliation(monkeypatch):
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        return [_repo("Pappu246/private-project")]

    monkeypatch.setattr("autonomous_agent.project_registry.gh_get", fake_get)
    monkeypatch.setenv("GITHUB_TOKEN", "present-but-never-logged")
    repos = discover_repositories("Pappu246")

    assert repos is not None
    assert repos[0]["full_name"] == "Pappu246/private-project"
    assert calls[0][0] == "/user/repos"
    assert calls[0][1]["affiliation"] == "owner"


def test_registry_reports_new_changed_and_removed_projects(monkeypatch):
    current = [_repo("Pappu246/solo-ai-v2"), _repo("Pappu246/new-project", fingerprint_seed="b")]
    monkeypatch.setattr("autonomous_agent.project_registry.discover_repositories", lambda owner: current)

    previous = {
        "Pappu246/solo-ai-v2": {"full_name": "Pappu246/solo-ai-v2", "fingerprint": "different"},
        "Pappu246/removed-project": {"full_name": "Pappu246/removed-project", "fingerprint": "old"},
    }
    profiles, changes = build_project_registry("Pappu246", previous)

    assert set(profiles) == {"Pappu246/solo-ai-v2", "Pappu246/new-project"}
    assert changes["new"] == ["Pappu246/new-project"]
    assert changes["changed"] == ["Pappu246/solo-ai-v2"]
    assert changes["removed"] == ["Pappu246/removed-project"]
