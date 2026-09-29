from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autonomous_agent.run_journal import (
    RunJournalRecord,
    append_run_record,
    make_run_record,
    read_run_records,
    summarize_run_records,
)


def test_append_run_record_writes_jsonl(tmp_path):
    path = tmp_path / "runs.jsonl"
    record = RunJournalRecord("abc", "inspect repository", "verified", "ok", 1, 1, "2026-01-01T00:00:00+00:00")
    append_run_record(path, record)
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["execution_id"] == "abc"
    assert row["state"] == "verified"


def test_make_run_record_normalizes_task():
    result = SimpleNamespace(state=SimpleNamespace(value="blocked"), reason="no access", attempts=0, results=())
    record = make_run_record(execution_id="x", task="  read   mailbox ", result=result)
    assert record.task == "read mailbox"
    assert record.result_count == 0


def test_append_run_record_rejects_oversized_record(tmp_path):
    path = tmp_path / "runs.jsonl"
    record = RunJournalRecord("x", "a" * 20_000, "blocked", "reason", 0, 0, "now")
    with pytest.raises(ValueError, match="size limit"):
        append_run_record(path, record)


def test_read_run_records_skips_invalid_lines_and_respects_limit(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text(
        "not-json\n"
        + json.dumps({
            "execution_id": "a",
            "task": "first",
            "state": "verified",
            "reason": "ok",
            "attempts": 1,
            "result_count": 1,
            "recorded_at": "2026-01-01T00:00:00+00:00",
        })
        + "\n"
        + json.dumps({"execution_id": "missing-fields"})
        + "\n"
        + json.dumps({
            "execution_id": "b",
            "task": "second",
            "state": "blocked",
            "reason": "denied",
            "attempts": 0,
            "result_count": 0,
            "recorded_at": "2026-01-01T00:01:00+00:00",
        })
        + "\n",
        encoding="utf-8",
    )

    records = read_run_records(path, limit=1)
    assert len(records) == 1
    assert records[0].execution_id == "a"
    assert read_run_records(tmp_path / "missing.jsonl") == ()


def test_read_run_records_rejects_invalid_limit(tmp_path):
    path = tmp_path / "runs.jsonl"
    with pytest.raises(ValueError, match="between 1 and 1000"):
        read_run_records(path, limit=0)
    with pytest.raises(ValueError, match="between 1 and 1000"):
        read_run_records(path, limit=1001)


def test_summarize_run_records_counts_supported_states():
    records = (
        RunJournalRecord("1", "one", "verified", "ok", 1, 1, "now"),
        RunJournalRecord("2", "two", "blocked", "denied", 0, 0, "now"),
        RunJournalRecord("3", "three", "failed", "error", 1, 0, "now"),
        RunJournalRecord("4", "four", "other", "ignored", 1, 0, "now"),
    )
    assert summarize_run_records(records) == {
        "total": 4,
        "verified": 1,
        "blocked": 1,
        "failed": 1,
    }


def _append_journal_records(path_str: str, worker: int) -> None:
    from pathlib import Path
    from autonomous_agent.run_journal import RunJournalRecord, append_run_record
    path = Path(path_str)
    for index in range(30):
        append_run_record(
            path,
            RunJournalRecord(
                execution_id=f"{worker}-{index}",
                task="inspect repository",
                state="verified",
                reason="ok",
                attempts=1,
                result_count=1,
                recorded_at="2026-01-01T00:00:00+00:00",
            ),
        )


def test_run_journal_survives_concurrent_processes(tmp_path):
    from multiprocessing import Process
    path = tmp_path / "runs.jsonl"
    workers = [Process(target=_append_journal_records, args=(str(path), worker)) for worker in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    records = read_run_records(path, limit=1000)
    assert len(records) == 120
