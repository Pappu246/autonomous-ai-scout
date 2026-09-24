from pathlib import Path
from autonomous_agent.sandbox import SAFE_OPERATIONS, run_safe_operation, to_execution_record

def test_sandbox_has_explicit_safe_operation_allowlist():
    assert SAFE_OPERATIONS == {"inspect", "test", "lint", "metrics", "read_file", "benchmark", "web_research", "rest", "filesystem_workspace", "workspace_shell", "gmail", "calendar", "browser"}
def test_sandbox_rejects_arbitrary_command_names(tmp_path: Path):
    result=run_safe_operation("rm -rf /",tmp_path); assert not result.success and result.verification_status=="blocked"
def test_sandbox_inspect_produces_read_only_repository_findings(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('ok')", encoding="utf-8")
    result = run_safe_operation("inspect", tmp_path)
    assert result.success
    assert result.network_disabled
    assert "Repository inspection completed in read-only mode." in result.output
    assert "Top findings:" in result.output
    assert "Empty Python dependency manifest" in result.output
    assert "No test directory detected" in result.output
    assert "No source files were modified." in result.output
def test_sandbox_metrics_counts_files(tmp_path: Path):
    (tmp_path/"a.txt").write_text("123",encoding="utf-8");r=run_safe_operation("metrics",tmp_path);assert r.success and "files=1" in r.output and "total_bytes=3" in r.output
def test_sandbox_read_file_blocks_escape(tmp_path: Path):
    outside=tmp_path.parent/"outside-sandbox.txt";outside.write_text("secret",encoding="utf-8")
    try:r=run_safe_operation("read_file",tmp_path,"../outside-sandbox.txt");assert not r.success and "escapes" in r.output
    finally:outside.unlink(missing_ok=True)
def test_sandbox_read_file_works_for_small_target(tmp_path: Path):
    (tmp_path/"hello.txt").write_text("hello",encoding="utf-8");r=run_safe_operation("read_file",tmp_path,"hello.txt");assert r.success and r.output=="hello"
def test_sandbox_filesystem_read_output_is_human_readable(tmp_path: Path):
    (tmp_path / "README.md").write_text("Line one\nLine two\n│ tree", encoding="utf-8")
    from autonomous_agent.filesystem_workspace import WorkspaceConnector

    result = run_safe_operation(
        "filesystem_workspace",
        tmp_path,
        workspace_connector=WorkspaceConnector(tmp_path),
        workspace_request={"operation": "read", "path": "README.md"},
    )
    assert result.success
    assert result.output.startswith("READ VERIFIED: README.md")
    assert "Line one\nLine two\n│ tree" in result.output
    assert "\\n" not in result.output
    assert "\\u2502" not in result.output


def test_sandbox_filesystem_transform_output_is_human_readable(tmp_path: Path):
    (tmp_path / "README.md").write_text("old text\n│ section", encoding="utf-8")
    from autonomous_agent.filesystem_workspace import WorkspaceConnector

    result = run_safe_operation(
        "filesystem_workspace",
        tmp_path,
        workspace_connector=WorkspaceConnector(tmp_path),
        workspace_request={
            "operation": "transform",
            "path": "README.md",
            "find": "old text",
            "replace": "new text",
        },
    )
    assert result.success
    assert result.output.startswith("TRANSFORM VERIFIED: README.md")
    assert "new text\n│ section" in result.output
    assert "\\n" not in result.output
    assert "\\u2502" not in result.output


def test_sandbox_inspect_and_read_file_hide_sensitive_paths(tmp_path: Path):
    (tmp_path / ".env").write_text("TOKEN=supersecret", encoding="utf-8")
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    inspected = run_safe_operation("inspect", tmp_path)
    assert "safe.txt" in inspected.output
    assert ".env" not in inspected.output
    blocked = run_safe_operation("read_file", tmp_path, ".env")
    assert not blocked.success
    assert "sensitive" in blocked.output


def test_sandbox_lint_detects_syntax_error(tmp_path: Path):
    (tmp_path/"broken.py").write_text("def broken(:\n",encoding="utf-8");r=run_safe_operation("lint",tmp_path);assert not r.success and r.exit_status==1 and r.verification_status=="failed"
