from __future__ import annotations

import json

from autonomous_agent import github_audit


def test_incomplete_profile_refresh_preserves_previous_baseline(monkeypatch, tmp_path):
    registry_path = tmp_path / "registry.json"
    profiles_path = tmp_path / "profiles.json"
    intelligence_path = tmp_path / "intelligence.json"
    previous = {
        "Pappu246/demo": {
            "full_name": "Pappu246/demo",
            "fingerprint": "known-good",
            "has_readme": True,
        }
    }
    profiles_path.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(github_audit, "PROJECT_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(github_audit, "PROJECT_PROFILES_PATH", profiles_path)
    monkeypatch.setattr(github_audit, "PROJECT_INTELLIGENCE_PATH", intelligence_path)
    monkeypatch.setattr(github_audit, "build_project_registry", lambda owner, old: ({"Pappu246/demo": {"private": False}}, {"new": [], "changed": ["Pappu246/demo"], "removed": []}))
    monkeypatch.setattr(github_audit, "build_project_profile", lambda name, metadata: {"full_name": name, "incomplete": True})
    monkeypatch.setattr(github_audit, "audit_repository", lambda name: [])

    findings = github_audit.audit_owner("Pappu246")
    saved = json.loads(profiles_path.read_text(encoding="utf-8"))

    assert saved == previous
    assert any(f["title"] == "Project technical profile refresh incomplete" for f in findings)
