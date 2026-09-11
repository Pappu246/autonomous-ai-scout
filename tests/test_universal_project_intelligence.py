from autonomous_agent.universal_project_intelligence import build_universal_intelligence


def test_python_project_without_lockfile_gets_reproducibility_signal():
    profile = {"full_name": "demo/python", "ecosystems": ["python"], "lockfiles": [], "has_readme": True, "has_license": True, "has_ci_hint": True, "private": False, "archived": False, "fingerprint": "abc"}
    result = build_universal_intelligence(profile)
    assert "dependencies_without_lockfile" in result["signals"]
    assert "reproducibility" in result["priorities"]
    assert result["score"] == 85


def test_complete_project_has_clean_baseline():
    profile = {"full_name": "demo/complete", "ecosystems": ["node"], "lockfiles": ["package-lock.json"], "has_readme": True, "has_license": True, "has_ci_hint": True, "private": False, "archived": False, "fingerprint": "xyz"}
    result = build_universal_intelligence(profile)
    assert result["signals"] == []
    assert result["priorities"] == []
    assert result["score"] == 100


def test_private_project_does_not_flag_missing_license():
    profile = {"full_name": "demo/private", "ecosystems": [], "lockfiles": [], "has_readme": True, "has_license": False, "has_ci_hint": True, "private": True, "archived": False, "fingerprint": "private"}
    result = build_universal_intelligence(profile)
    assert "missing_license" not in result["signals"]
