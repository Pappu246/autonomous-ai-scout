from __future__ import annotations

from autonomous_agent.project_profile import build_project_profile


def test_project_profile_detects_ecosystems_lockfiles_and_signals(monkeypatch):
    def fake_get(path, params=None):
        if path.endswith("/contents/"):
            return [
                {"name": "README.md", "type": "file"},
                {"name": "package.json", "type": "file"},
                {"name": "package-lock.json", "type": "file"},
                {"name": ".github", "type": "dir"},
                {"name": "LICENSE", "type": "file"},
            ]
        if path.endswith("/languages"):
            return {"TypeScript": 1200, "JavaScript": 300}
        raise AssertionError(path)

    monkeypatch.setattr("autonomous_agent.project_profile.gh_get", fake_get)
    profile = build_project_profile("Pappu246/demo", {"visibility": "public", "private": False, "default_branch": "main"})
    assert profile["ecosystems"] == ["node"]
    assert profile["lockfiles"] == ["package-lock.json"]
    assert profile["has_readme"] is True
    assert profile["has_license"] is True
    assert profile["has_ci_hint"] is True
    assert profile["languages"]["TypeScript"] == 1200
    assert profile["incomplete"] is False
    assert profile["fingerprint"]


def test_project_profile_marks_api_failures_incomplete(monkeypatch):
    monkeypatch.setattr("autonomous_agent.project_profile.gh_get", lambda path, params=None: None)
    profile = build_project_profile("Pappu246/unavailable", {})
    assert profile["ecosystems"] == []
    assert profile["languages"] == {}
    assert profile["has_readme"] is False
    assert profile["incomplete"] is True


def test_project_profile_respects_registry_license_signal(monkeypatch):
    monkeypatch.setattr("autonomous_agent.project_profile.gh_get", lambda path, params=None: [] if path.endswith("/contents/") else {})
    profile = build_project_profile("Pappu246/licensed", {"license": {"spdx_id": "MIT"}})
    assert profile["has_license"] is True
    assert profile["incomplete"] is False
