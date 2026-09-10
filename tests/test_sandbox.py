from __future__ import annotations

from pathlib import Path

from autonomous_agent.sandbox import SAFE_OPERATIONS, run_safe_operation, to_execution_record


def test_sandbox_has_explicit_safe_operation_allowlist():
    assert SAFE_OPERATIONS == {"inspect", "test", "lint", "metrics", "read_file", "benchmark"}


def test_sandbox_rejects_arbitrary_command_names(tmp_path: Path):
    result = run_safe_operation("rm -rf /", tmp_path)
    assert not result.success
    assert result.verification_status == "blocked"


def test_sandbox_inspect_is_deterministic(tmp_path: Path):
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    result = run_safe_operation("inspect", tmp_path)
    assert result.success
    assert result.output.splitlines() == ["Workspace files:", "- a.txt", "- b.txt"]
    assert result.network_disabled


def test_sandbox_metrics_counts_files(tmp_path: Path):
    (tmp_path / "a.txt").write_text("123", encoding="utf-8")
    result = run_safe_operation("metrics", tmp_path)
    assert result.success
    assert "files=1" in result.output
    assert "total_bytes=3" in result.output


def test_sandbox_read_file_blocks_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside-sandbox.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        result = run_safe_operation("read_file", tmp_path, "../outside-sandbox.txt")
        assert not result.success
        assert "escapes" in result.output
    finally:
        outside.unlink(missing_ok=True)


def test_sandbox_read_file_works_for_small_target(tmp_path: Path):
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    result = run_safe_operation("read_file", tmp_path, "hello.txt")
    assert result.success
    assert result.output == "hello"


def test_sandbox_lint_detects_syntax_error(tmp_path: Path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    result = run_safe_operation("lint", tmp_path)
    assert not result.success
    assert result.exit_status == 1
    assert result.verification_status == "failed"


def test_sandbox_lint_passes_valid_python(tmp_path: Path):
    (tmp_path / "ok.py").write_text("value = 1\n", encoding="utf-8")
    result = run_safe_operation("lint", tmp_path)
    assert result.success
    assert result.exit_status == 0


def test_sandbox_benchmark_is_local_and_deterministic(tmp_path: Path):
    first = run_safe_operation("benchmark", tmp_path)
    second = run_safe_operation("benchmark", tmp_path)
    assert first.success and second.success
    assert first.output == second.output
    assert first.network_disabled


def test_sandbox_output_is_bounded(tmp_path: Path):
    (tmp_path / "large.txt").write_text("x" * 5000, encoding="utf-8")
    result = run_safe_operation("read_file", tmp_path, "large.txt", output_limit=100)
    assert len(result.output.encode("utf-8")) <= 100
    assert result.output_truncated


def test_execution_record_contains_metadata_not_secret_material(tmp_path: Path):
    (tmp_path / "ok.txt").write_text("ok", encoding="utf-8")
    result = run_safe_operation("read_file", tmp_path, "ok.txt")
    record = to_execution_record("action-1", "approval-digest", result)
    assert record.action_id == "action-1"
    assert record.approval_id == "approval-digest"
    assert record.verification_status == "verified"
