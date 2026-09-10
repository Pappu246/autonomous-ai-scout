from pathlib import Path

import pytest

from autonomous_agent.approval_audit import append_decision, load_audit_log


def test_append_and_load_decision(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    append_decision(path, "abc123", "approved")
    entries = load_audit_log(path)
    assert len(entries) == 1
    assert entries[0]["action_id"] == "abc123"
    assert entries[0]["decision"] == "approved"
    assert entries[0]["recorded_at"]


def test_reject_invalid_decision(tmp_path: Path):
    with pytest.raises(ValueError):
        append_decision(tmp_path / "audit.jsonl", "abc123", "execute")


def test_malformed_lines_are_ignored(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    path.write_text("not-json\n{\"action_id\": \"x\"}\n", encoding="utf-8")
    assert load_audit_log(path) == []
