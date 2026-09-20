from __future__ import annotations

from pathlib import Path

from autonomous_agent.doctor import run_checks


def test_doctor_detects_missing_operational_configuration(tmp_path: Path, monkeypatch):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")

    for key in (
        "CODING_PROVIDER_1_NAME",
        "CODING_PROVIDER_1_ENDPOINT",
        "CODING_PROVIDER_1_MODEL",
        "CODING_PROVIDER_1_API_KEY_ENV",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    checks = {item.name: item for item in run_checks(tmp_path)}

    assert checks["python"].ok
    assert checks["workspace"].ok
    assert checks["ci workflow"].ok
    assert checks["coding providers"].ok is False
    assert checks["github credential"].ok is False


def test_doctor_reports_configured_provider_without_exposing_secret(tmp_path, monkeypatch):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")

    monkeypatch.setenv("CODING_PROVIDER_1_NAME", "demo")
    monkeypatch.setenv("CODING_PROVIDER_1_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("CODING_PROVIDER_1_MODEL", "demo")
    monkeypatch.setenv("CODING_PROVIDER_1_API_KEY_ENV", "DEMO_API_KEY")
    monkeypatch.setenv("DEMO_API_KEY", "do-not-print")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")

    checks = {item.name: item for item in run_checks(tmp_path)}

    assert checks["coding providers"].ok
    assert checks["github credential"].ok
    assert "do-not-print" not in checks["coding providers"].detail
    assert "github-secret" not in checks["github credential"].detail
