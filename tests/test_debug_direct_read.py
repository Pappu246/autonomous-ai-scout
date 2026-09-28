from pathlib import Path
from autonomous_agent.runtime import run_task
from autonomous_agent.execution_engine import ExecutionState

def test_debug_direct_read_failure(tmp_path: Path):
    (tmp_path / "README.md").write_text("Line one\nLine two\n│ section", encoding="utf-8")
    result = run_task(
        "Read README.md and give me a human-readable summary. Do not modify any files.",
        root=tmp_path,
        audit_path=tmp_path / "debug.jsonl",
        journal_path=tmp_path / "debug-journal.jsonl",
        execution_id="debug-direct-read",
    )
    assert result.state is ExecutionState.VERIFIED, (result.results[-1].command, result.results[-1].output, result.reason) if result.results else result.reason
