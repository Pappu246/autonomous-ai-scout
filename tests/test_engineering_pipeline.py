from __future__ import annotations

from pathlib import Path

from autonomous_agent.engineering_pipeline import LocalRepositoryReader, build_local_coding_pipeline


def test_local_repository_reader_blocks_workspace_escape(tmp_path: Path):
    reader = LocalRepositoryReader(tmp_path)
    (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
    assert reader("owner/repo", "app.py") == "print(1)\n"

    try:
        reader("owner/repo", "../outside.py")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("workspace escape must be rejected")


def test_local_coding_pipeline_fails_closed_without_provider_credentials(tmp_path: Path, monkeypatch):
    for key in (
        "CODING_PROVIDER_1_NAME",
        "CODING_PROVIDER_1_ENDPOINT",
        "CODING_PROVIDER_1_MODEL",
        "CODING_PROVIDER_1_API_KEY_ENV",
    ):
        monkeypatch.delenv(key, raising=False)

    pipeline = build_local_coding_pipeline(tmp_path)
    result = pipeline.run_finding(
        project="owner/repo",
        severity="medium",
        title="test regression",
        detail="tests need attention",
        recommendation="add regression coverage",
        affected_area=("test_app.py",),
    )

    assert result.candidate is None
    assert result.approval_required is True
