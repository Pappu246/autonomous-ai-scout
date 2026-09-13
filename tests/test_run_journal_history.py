from pathlib import Path

import pytest

from autonomous_agent.run_journal import RunJournalRecord, append_run_record, read_run_records, summarize_run_records


def _record(execution_id: str, state: str) -> RunJournalRecord:
    return RunJournalRecord(execution_id, "inspect repository", state, "ok", 1, 1, "2026-01-01T00:00:00+00:00")


def test_read_run_records_respects_limit_and_skips_invalid_lines(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    append_run_record(path, _record("a", "verified"))
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")
    append_run_record(path, _record("b", "blocked"))
    records = read_run_records(path, limit=2)
    assert [record.execution_id for record in records] == ["a", "b"]


def test_read_run_records_missing_file_is_empty(tmp_path: Path):
    assert read_run_records(tmp_path / "missing.jsonl") == ()


def test_read_run_records_rejects_unbounded_limits(tmp_path: Path):
    with pytest.raises(ValueError):
        read_run_records(tmp_path / "runs.jsonl", limit=0)
    with pytest.raises(ValueError):
        read_run_records(tmp_path / "runs.jsonl", limit=1001)


def test_summarize_run_records_counts_states():
    records = (_record("a", "verified"), _record("b", "blocked"), _record("c", "failed"), _record("d", "verified"))
    assert summarize_run_records(records) == {"total": 4, "verified": 2, "blocked": 1, "failed": 1}
