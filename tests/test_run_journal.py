from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autonomous_agent.run_journal import RunJournalRecord, append_run_record, make_run_record


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
