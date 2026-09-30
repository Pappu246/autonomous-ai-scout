from pathlib import Path

from autonomous_agent.dependency_security import analyze_dependencies


def test_requirements_lock_suppresses_missing_lockfile_finding(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    (tmp_path / "requirements.lock").write_text("demo==1.0.0\n", encoding="utf-8")
    findings = analyze_dependencies(tmp_path, "fixture")
    assert not any(f.title == "No dependency lockfile detected" for f in findings)
