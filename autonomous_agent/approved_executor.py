from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .action_queue import PendingAction
from .approval_audit import load_audit_log, verify_audit_chain


APPROVAL_TTL = timedelta(hours=24)
_ALLOWED_ACTIONS = {"read", "inspect", "test", "lint", "benchmark"}
_BLOCKED_TERMS = (
    "deploy", "merge", "release", "push", "force", "billing", "payment",
    "credential", "secret", "password", "token", "production", "delete",
    "remove", "write", "modify", "edit", "install", "uninstall",
)


@dataclass(frozen=True)
class ApprovalRecord:
    action_id: str
    approved_at: str
    expires_at: str
    approval_token: str
    action_digest: str = ""

    @classmethod
    def for_action(
        cls,
        action: PendingAction,
        approval_token: str,
        approved_at: datetime | None = None,
        ttl: timedelta = APPROVAL_TTL,
    ) -> "ApprovalRecord":
        approved = approved_at or datetime.now(timezone.utc)
        expires = approved + ttl
        return cls(
            action_id=action.id,
            approved_at=approved.isoformat(),
            expires_at=expires.isoformat(),
            approval_token=approval_token,
            action_digest=action_fingerprint(action),
        )


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str


def action_fingerprint(action: PendingAction) -> str:
    """Stable digest of the complete action identity and requested work."""
    payload = json.dumps(
        {
            "id": action.id,
            "task": action.task,
            "steps": list(action.steps),
            "risk": action.risk,
            "reason": action.reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approval_claim_id(approval: ApprovalRecord) -> str:
    """Return a non-sensitive, stable identifier used to consume an approval once."""
    token = approval.approval_token.strip()
    if not token:
        raise ValueError("approval token is missing")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def claim_approval(approval: ApprovalRecord, store: Path) -> ExecutionDecision:
    """Atomically consume an approval token; a claimed token can never be replayed."""
    if not approval.approval_token.strip():
        return ExecutionDecision(False, "approval token is missing")
    try:
        store.mkdir(parents=True, exist_ok=True)
        marker = store / f"{approval_claim_id(approval)}.claimed"
        with marker.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "action_id": approval.action_id,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }, sort_keys=True) + "\n")
    except FileExistsError:
        return ExecutionDecision(False, "approval has already been consumed")
    except OSError as exc:
        return ExecutionDecision(False, f"approval consumption store is unavailable: {exc}")
    return ExecutionDecision(True, "approval consumed exactly once")


def _blocked(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _BLOCKED_TERMS)


def _safe_step(step: str) -> bool:
    if _blocked(step):
        return False
    words = {part.strip(".,:;()[]{}") for part in step.lower().split()}
    return bool(words & _ALLOWED_ACTIONS)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def validate_approval(
    action: PendingAction,
    approval: ApprovalRecord,
    now: datetime | None = None,
    audit_path: Path | None = None,
) -> ExecutionDecision:
    """Require exact identity, immutable action binding, valid timestamps and optional verified audit."""
    if action.status != "approved":
        return ExecutionDecision(False, "action is not explicitly approved")
    if approval.action_id != action.id:
        return ExecutionDecision(False, "approval does not match action identity")
    if approval.action_digest != action_fingerprint(action):
        return ExecutionDecision(False, "approval is not bound to the current action definition")
    if not approval.approval_token.strip():
        return ExecutionDecision(False, "approval token is missing")
    approved_at = _parse_timestamp(approval.approved_at)
    expires = _parse_timestamp(approval.expires_at)
    if approved_at is None or expires is None or expires <= approved_at:
        return ExecutionDecision(False, "approval timestamps are invalid")
    current = now or datetime.now(timezone.utc)
    if current < approved_at:
        return ExecutionDecision(False, "approval is not active yet")
    if current >= expires:
        return ExecutionDecision(False, "approval has expired")
    if audit_path is not None:
        if not verify_audit_chain(audit_path):
            return ExecutionDecision(False, "approval audit chain is invalid")
        approvals = [
            entry for entry in load_audit_log(audit_path)
            if entry["action_id"] == action.id
        ]
        if not approvals or approvals[-1]["decision"] != "approved":
            return ExecutionDecision(False, "approval is not backed by the latest audit decision")
    return ExecutionDecision(True, "explicit approval is valid")


def authorize_execution(
    action: PendingAction,
    approval: ApprovalRecord,
    now: datetime | None = None,
    audit_path: Path | None = None,
) -> ExecutionDecision:
    """Validate approval and independently re-check every requested step at execution time."""
    decision = validate_approval(action, approval, now, audit_path)
    if not decision.allowed:
        return decision
    if _blocked(action.task):
        return ExecutionDecision(False, "task crosses a forbidden execution boundary")
    if not action.steps:
        return ExecutionDecision(False, "no executable steps were supplied")
    if any(not _safe_step(step) for step in action.steps):
        return ExecutionDecision(False, "one or more steps are outside the safe execution allowlist")
    return ExecutionDecision(True, "approved action is limited to safe local read-only/test operations")


def execute_approved_action(
    action: PendingAction,
    approval: ApprovalRecord,
    root: Path,
    now: datetime | None = None,
    audit_path: Path | None = None,
) -> ExecutionDecision:
    """Authorization boundary only; concrete sandbox execution is implemented separately."""
    decision = authorize_execution(action, approval, now, audit_path)
    if not decision.allowed:
        return decision
    if not root.exists() or not root.is_dir():
        return ExecutionDecision(False, "execution root is not a valid project directory")
    return ExecutionDecision(True, "authorized; concrete executor remains intentionally read-only/test-only")
