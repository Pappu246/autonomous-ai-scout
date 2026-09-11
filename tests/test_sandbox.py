from pathlib import Path
from autonomous_agent.sandbox import SAFE_OPERATIONS, run_safe_operation, to_execution_record

def test_sandbox_has_explicit_safe_operation_allowlist():
    assert SAFE_OPERATIONS == {"inspect", "test", "lint", "metrics", "read_file", "benchmark", "web_research"}
def test_sandbox_rejects_arbitrary_command_names(tmp_path: Path):
    result=run_safe_operation("rm -rf /",tmp_path); assert not result.success and result.verification_status=="blocked"
def test_sandbox_inspect_is_deterministic(tmp_path: Path):
    (tmp_path/"b.txt").write_text("b",encoding="utf-8");(tmp_path/"a.txt").write_text("a",encoding="utf-8");r=run_safe_operation("inspect",tmp_path);assert r.success and r.output.splitlines()==["Workspace files:","- a.txt","- b.txt"] and r.network_disabled
def test_sandbox_metrics_counts_files(tmp_path: Path):
    (tmp_path/"a.txt").write_text("123",encoding="utf-8");r=run_safe_operation("metrics",tmp_path);assert r.success and "files=1" in r.output and "total_bytes=3" in r.output
def test_sandbox_read_file_blocks_escape(tmp_path: Path):
    outside=tmp_path.parent/"outside-sandbox.txt";outside.write_text("secret",encoding="utf-8")
    try:r=run_safe_operation("read_file",tmp_path,"../outside-sandbox.txt");assert not r.success and "escapes" in r.output
    finally:outside.unlink(missing_ok=True)
def test_sandbox_read_file_works_for_small_target(tmp_path: Path):
    (tmp_path/"hello.txt").write_text("hello",encoding="utf-8");r=run_safe_operation("read_file",tmp_path,"hello.txt");assert r.success and r.output=="hello"
def test_sandbox_lint_detects_syntax_error(tmp_path: Path):
    (tmp_path/"broken.py").write_text("def broken(:\n",encoding="utf-8");r=run_safe_operation("lint",tmp_path);assert not r.success and r.exit_status==1 and r.verification_status=="failed"
