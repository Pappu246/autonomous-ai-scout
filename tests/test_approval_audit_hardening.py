from pathlib import Path

import pytest

from autonomous_agent.approval_audit import append_decision, verify_audit_chain


def test_approval_audit_refuses_to_extend_tampered_chain(tmp_path: Path):
    path = tmp_path / "approval.jsonl"
    append_decision(path, "one", "approved")
    raw = path.read_text(encoding="utf-8").replace('"approved"', '"rejected"', 1)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        append_decision(path, "two", "approved")
    assert not verify_audit_chain(path)
