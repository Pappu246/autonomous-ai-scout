from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .action_queue import PendingAction


APPROVAL_TTL = timedelta(hours=24)

# Only deterministic, local, read-only/test operations may cross this boundary.
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


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str


def _blocked(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _BLOCKED_TERMS)


def _safe_step(step: str) -> bool:
    if _blocked(step):
        return False
    words = {part.strip(".,:;()[]{}") for part in step.lower().split()}
    return bool(words & _ALLOWED_ACTIONS)


def validate_approval(action: PendingAction, approval: ApprovalRecord, now: datetime | None = None) -> ExecutionDecision:
    """Require exact identity, explicit token and a non-expired approval."""
    if action.status != "approved":
        return ExecutionDecision(False, "action is not explicitly approved")
    if approval.action_id != action.id:
        return ExecutionDecision(False, "approval does not match action identity")
    if not approval.approval_token.strip():
        return ExecutionDecision(False, "approval token is missing")
    current = now or datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(approval.expires_at.replace("Z", "+00:00"))
    except ValueError:
        return ExecutionDecision(False, "approval expiry is invalid")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if current >= expires:
        return ExecutionDecision(False, "approval has expired")
    return ExecutionDecision(True, "explicit approval is valid")


def authorize_execution(action: PendingAction, approval: ApprovalRecord, now: datetime | None = None) -> ExecutionDecision:
    """Validate approval and independently re-check every requested step at execution time."""
    decision = validate_approval(action, approval, now)
    if not decision.allowed:
        return decision
    if _blocked(action.task):
        return ExecutionDecision(False, "task crosses a forbidden execution boundary")
    if not action.steps:
        return ExecutionDecision(False, "no executable steps were supplied")
    if any(not _safe_step(step) for step in action.steps):
        return ExecutionDecision(False, "one or more steps are outside the safe execution allowlist")
    return ExecutionDecision(True, "approved action is limited to safe local read-only/test operations")


def execute_approved_action(action: PendingAction, approval: ApprovalRecord, root: Path, now: datetime | None = None) -> ExecutionDecision:
    """Authorization boundary only: no shell, network, source-write, merge, deploy, or billing execution."""
    decision = authorize_execution(action, approval, now)
    if not decision.allowed:
        return decision
    if not root.exists() or not root.is_dir():
        return ExecutionDecision(False, "execution root is not a valid project directory")
    return ExecutionDecision(True, "authorized; concrete executor remains intentionally read-only/test-only")
