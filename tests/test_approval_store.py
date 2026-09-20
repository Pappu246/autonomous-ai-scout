from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from autonomous_agent.action_queue import PendingAction
from autonomous_agent.approval import set_decision
from autonomous_agent.approval_audit import verify_audit_chain
from autonomous_agent.approval_store import create_approval, load_approval, reject_action


def write_queue(path: Path):
    path.write_text(
        json.dumps(
            [
                {
                    "id": "action-1",
                    "task": "prepare approved source improvement",
                    "steps": ["inspect", "test"],
                    "risk": "high",
                    "reason": "reviewed",
                    "status": "pending",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        ),
        encoding="utf-8",
    )


def test_set_decision_preserves_created_at_and_audits(tmp_path: Path):
    queue = tmp_path / "queue.json"
    audit = tmp_path / "audit.jsonl"
    write_queue(queue)

    from autonomous_agent.action_queue import load_queue
    before = load_queue(queue)[0]
    after = set_decision(queue, "action-1", "approved", audit)

    assert after.status == "approved"
    assert after.created_at == before.created_at
    assert verify_audit_chain(audit)


def test_create_and_reload_approval_record(tmp_path: Path):
    queue = tmp_path / "queue.json"
    audit = tmp_path / "audit.jsonl"
    approvals = tmp_path / "approvals"
    write_queue(queue)

    record = create_approval(queue, approvals, "action-1", audit_path=audit)

    assert record.action_id == "action-1"
    assert record.approval_token
    assert record.action_digest
    restored = load_approval(approvals, "action-1")
    assert restored == record
    assert (approvals / "action-1.json").exists()


def test_reject_action_is_explicit_and_does_not_create_approval(tmp_path: Path):
    queue = tmp_path / "queue.json"
    audit = tmp_path / "audit.jsonl"
    approvals = tmp_path / "approvals"
    write_queue(queue)

    result = reject_action(queue, "action-1", audit_path=audit)

    assert isinstance(result, PendingAction)
    assert result.status == "rejected"
    assert not approvals.exists()


def test_invalid_action_id_is_rejected(tmp_path: Path):
    queue = tmp_path / "queue.json"
    write_queue(queue)

    try:
        create_approval(queue, tmp_path / "approvals", "../action-1")
    except ValueError as exc:
        assert "invalid action id" in str(exc)
    else:
        raise AssertionError("path traversal action id must be rejected")
