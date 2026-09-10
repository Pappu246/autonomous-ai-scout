from __future__ import annotations

from autonomous_agent.project_profile import build_project_profile


def test_project_profile_classifies_ecosystems_and_signals(monkeypatch):
    def fake_get(path, params=None):
        if path.endswith("/contents/"):
            return [
                {"name": "README.md", "type": "file"},
                {"name": "package.json", "type": "file"},
                {"name": "package-lock.json", "type": "file"},
                {"name": "Dockerfile", "type": "file"},
                {"name": "LICENSE", "type": "file"},
            ]
        if path.endswith("/languages"):
            return {"TypeScript": 1200, "JavaScript": 300}
        raise AssertionError(path)

    monkeypatch.setattr("autonomous_agent.project_profile.gh_get", fake_get)
    profile = build_project_profile(
        "Pappu246/solo-ai-v2",
        {"visibility": "public", "private": False, "default_branch": "main", "license": None},
    )

    assert profile["ecosystems"] == ["node", "container"]
    assert profile["lockfiles"] == ["package-lock.json"]
    assert profile["has_readme"] is True
    assert profile["has_license"] is True
    assert profile["has_ci_hint"] is False
    assert profile["languages"] == {"TypeScript": 1200, "JavaScript": 300}
    assert len(profile["fingerprint"]) == 64


def test_project_profile_handles_api_failures_as_empty_signals(monkeypatch):
    monkeypatch.setattr("autonomous_agent.project_profile.gh_get", lambda path, params=None: None)
    profile = build_project_profile("Pappu246/example", {})
    assert profile["root_files"] == []
    assert profile["languages"] == {}
    assert profile["ecosystems"] == []
    assert profile["has_readme"] is False
