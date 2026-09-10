from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from autonomous_agent.action_queue import PendingAction
from autonomous_agent.approved_executor import ApprovalRecord, approval_claim_id, claim_approval


def _action() -> PendingAction:
    return PendingAction(
        "action-1",
        "inspect repository",
        ("inspect repository",),
        "low",
        "safe diagnostics",
        "approved",
        datetime.now(timezone.utc).isoformat(),
    )


def test_claim_approval_is_one_time(tmp_path: Path):
    approval = ApprovalRecord.for_action(_action(), "unique-approval-token")
    first = claim_approval(approval, tmp_path / "claims")
    second = claim_approval(approval, tmp_path / "claims")
    assert first.allowed
    assert not second.allowed
    assert "consumed" in second.reason
    assert len(list((tmp_path / "claims").glob("*.claimed"))) == 1


def test_claim_does_not_store_raw_token(tmp_path: Path):
    approval = ApprovalRecord.for_action(_action(), "super-secret-token")
    claim_approval(approval, tmp_path / "claims")
    raw = (tmp_path / "claims" / f"{approval_claim_id(approval)}.claimed").read_text(encoding="utf-8")
    assert "super-secret-token" not in raw
    assert approval.action_id in raw


def test_empty_token_cannot_be_claimed(tmp_path: Path):
    approval = ApprovalRecord("action-1", "2026-09-10T10:00:00+00:00", "2026-09-10T11:00:00+00:00", "", "digest")
    decision = claim_approval(approval, tmp_path / "claims")
    assert not decision.allowed
    assert "missing" in decision.reason
