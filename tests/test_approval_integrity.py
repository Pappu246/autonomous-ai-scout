from datetime import datetime, timedelta, timezone
from pathlib import Path

from autonomous_agent.action_queue import PendingAction
from autonomous_agent.approval_audit import append_decision, verify_audit_chain
from autonomous_agent.approved_executor import ApprovalRecord, action_fingerprint, validate_approval


def _action(**overrides: object) -> PendingAction:
    values = {
        "id": "action-1",
        "task": "inspect repository",
        "steps": ("read files", "run tests"),
        "risk": "low",
        "reason": "safe diagnostics",
        "status": "approved",
        "created_at": "2026-09-10T10:00:00+00:00",
    }
    values.update(overrides)
    return PendingAction(**values)


def test_for_action_binds_complete_action():
    action = _action()
    approval = ApprovalRecord.for_action(
        action,
        "opaque-token",
        datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
    )
    assert approval.action_id == action.id
    assert approval.action_digest == action_fingerprint(action)


def test_rejects_tampered_action_definition():
    action = _action()
    approval = ApprovalRecord.for_action(action, "opaque-token")
    tampered = _action(task="inspect repository and deploy")
    decision = validate_approval(tampered, approval)
    assert not decision.allowed
    assert "bound" in decision.reason


def test_rejects_expiry_before_approval():
    action = _action()
    approved_at = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
    approval = ApprovalRecord(
        action.id,
        approved_at.isoformat(),
        (approved_at - timedelta(minutes=1)).isoformat(),
        "opaque-token",
        action_fingerprint(action),
    )
    decision = validate_approval(action, approval, approved_at)
    assert not decision.allowed


def test_audit_chain_detects_tampering(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    append_decision(path, "action-1", "approved")
    append_decision(path, "action-2", "rejected")
    assert verify_audit_chain(path)

    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"approved"', '"rejected"', 1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not verify_audit_chain(path)


def test_audit_chain_rejects_malformed_line(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    assert not verify_audit_chain(path)
