from pathlib import Path

from autonomous_agent.project_intelligence import analyze_project


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_secret_scanner_ignores_test_fixture_like_token(tmp_path):
    _write(tmp_path / "fixture.py", 'token = "validated maintenance improvement"\n')
    findings = analyze_project(tmp_path, "fixture")
    assert not any(f.title == "Possible hard-coded secret" for f in findings)


def test_secret_scanner_detects_high_confidence_token(tmp_path):
    _write(tmp_path / "config.py", 'token = "aB7!xQ92_secret_value_2026"\n')
    findings = analyze_project(tmp_path, "fixture")
    assert any(f.title == "Possible hard-coded secret" for f in findings)


def test_secret_scanner_detects_known_provider_prefix(tmp_path):
    _write(tmp_path / "config.py", 'api_key = "sk-test_abcdefghijklmnop"\n')
    findings = analyze_project(tmp_path, "fixture")
    assert any(f.title == "Possible hard-coded secret" for f in findings)
