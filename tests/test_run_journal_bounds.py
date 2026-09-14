from autonomous_agent.run_journal import MAX_JOURNAL_BYTES, RunJournalRecord, append_run_record


def _record(execution_id: str, task: str = "run tests") -> RunJournalRecord:
    return RunJournalRecord(execution_id, task, "verified", "ok", 1, 1, "2026-09-14T00:00:00+00:00")


def test_journal_stays_within_bounded_size(tmp_path):
    path = tmp_path / "runtime.jsonl"
    for index in range(100):
        append_run_record(path, _record(str(index), "x" * 10000))

    assert path.stat().st_size <= MAX_JOURNAL_BYTES
    assert path.read_bytes().endswith(b"\n")


def test_compaction_preserves_newest_record(tmp_path):
    path = tmp_path / "runtime.jsonl"
    for index in range(100):
        append_run_record(path, _record(str(index), "x" * 10000))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines
    assert '"execution_id":"99"' in lines[-1]


def test_small_journal_is_not_compacted(tmp_path):
    path = tmp_path / "runtime.jsonl"
    append_run_record(path, _record("first"))
    append_run_record(path, _record("second"))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"execution_id":"first"' in lines[0]
    assert '"execution_id":"second"' in lines[1]
