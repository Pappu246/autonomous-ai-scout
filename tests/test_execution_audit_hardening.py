from pathlib import Path

import pytest

from autonomous_agent.execution_audit import append_execution_record, verify_execution_audit


def test_execution_audit_refuses_to_extend_tampered_chain(tmp_path: Path):
    path = tmp_path / "execution.jsonl"
    append_execution_record(path, {"execution_id": "one", "state": "running"})
    raw = path.read_text(encoding="utf-8").replace('"running"', '"tampered"', 1)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        append_execution_record(path, {"execution_id": "two", "state": "running"})
    assert not verify_execution_audit(path)
