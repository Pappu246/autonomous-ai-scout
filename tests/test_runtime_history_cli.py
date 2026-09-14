from pathlib import Path

from autonomous_agent.run_journal import RunJournalRecord, append_run_record
from autonomous_agent.runtime import main


def _record(execution_id: str, state: str) -> RunJournalRecord:
    return RunJournalRecord(execution_id, f"task {execution_id}", state, "ok", 1, 1, "2026-01-01T00:00:00+00:00")


def test_runtime_history_cli_prints_summary_and_records(tmp_path: Path, capsys):
    journal = tmp_path / "runs.jsonl"
    append_run_record(journal, _record("one", "verified"))
    append_run_record(journal, _record("two", "blocked"))

    assert main(["--history", "--journal", str(journal), "--history-limit", "10"]) == 0
    output = capsys.readouterr().out

    assert "total=2" in output
    assert "verified=1" in output
    assert "blocked=1" in output
    assert "failed=0" in output
    assert "execution_id=one" in output
    assert "execution_id=two" in output


def test_runtime_history_cli_handles_missing_journal(tmp_path: Path, capsys):
    journal = tmp_path / "missing.jsonl"

    assert main(["--history", "--journal", str(journal)]) == 0
    output = capsys.readouterr().out

    assert "total=0" in output
    assert "verified=0" in output
    assert "blocked=0" in output
    assert "failed=0" in output
