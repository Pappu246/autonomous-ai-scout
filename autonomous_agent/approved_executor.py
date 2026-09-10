from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .action_queue import PendingAction
from .approval_audit import load_audit_log, verify_audit_chain
from .capability_policy import Capability, check_capability
from .lifecycle_integration import record_transition, require_state
from .action_lifecycle import LifecycleState
from .sandbox import ExecutionRecord, run_safe_operation, to_execution_record


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
    def for_action(cls, action: PendingAction, approval_token: str, approved_at: datetime | None = None, ttl: timedelta = APPROVAL_TTL) -> "ApprovalRecord":
        approved = approved_at or datetime.now(timezone.utc)
        expires = approved + ttl
        return cls(action.id, approved.isoformat(), expires.isoformat(), approval_token, action_fingerprint(action))


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str
    records: tuple[ExecutionRecord, ...] = ()


def action_fingerprint(action: PendingAction) -> str:
    payload = json.dumps({"id": action.id, "task": action.task, "steps": list(action.steps), "risk": action.risk, "reason": action.reason}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approval_claim_id(approval: ApprovalRecord) -> str:
    token = approval.approval_token.strip()
    if not token:
        raise ValueError("approval token is missing")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def claim_approval(approval: ApprovalRecord, store: Path) -> ExecutionDecision:
    if not approval.approval_token.strip():
        return ExecutionDecision(False, "approval token is missing")
    try:
        store.mkdir(parents=True, exist_ok=True)
        marker = store / f"{approval_claim_id(approval)}.claimed"
        with marker.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"action_id": approval.action_id, "claimed_at": datetime.now(timezone.utc).isoformat()}, sort_keys=True) + "\n")
    except FileExistsError:
        return ExecutionDecision(False, "approval has already been consumed")
    except OSError as exc:
        return ExecutionDecision(False, f"approval consumption store is unavailable: {exc}")
    return ExecutionDecision(True, "approval consumed exactly once")


def _blocked(text: str) -> bool:
    return any(term in text.lower() for term in _BLOCKED_TERMS)


def _safe_step(step: str) -> bool:
    if _blocked(step):
        return False
    words = {part.strip(".,:;()[]{}") for part in step.lower().split()}
    return bool(words & _ALLOWED_ACTIONS)


def _step_operation(step: str) -> tuple[str, str | None] | None:
    text = step.strip()
    lowered = text.lower()
    if lowered.startswith("read file:"):
        return ("read_file", text.split(":", 1)[1].strip() or None)
    if "inspect" in lowered:
        return ("inspect", None)
    if "test" in lowered or "pytest" in lowered:
        return ("test", None)
    if "lint" in lowered or "static analysis" in lowered:
        return ("lint", None)
    if "metric" in lowered or "calculate" in lowered:
        return ("metrics", None)
    if "benchmark" in lowered:
        return ("benchmark", None)
    return None


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def validate_approval(action: PendingAction, approval: ApprovalRecord, now: datetime | None = None, audit_path: Path | None = None) -> ExecutionDecision:
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
        approvals = [entry for entry in load_audit_log(audit_path) if entry["action_id"] == action.id]
        if not approvals or approvals[-1]["decision"] != "approved":
            return ExecutionDecision(False, "approval is not backed by the latest audit decision")
    return ExecutionDecision(True, "explicit approval is valid")


def authorize_execution(action: PendingAction, approval: ApprovalRecord, now: datetime | None = None, audit_path: Path | None = None) -> ExecutionDecision:
    decision = validate_approval(action, approval, now, audit_path)
    if not decision.allowed:
        return decision
    if _blocked(action.task):
        return ExecutionDecision(False, "task crosses a forbidden execution boundary")
    if not action.steps:
        return ExecutionDecision(False, "no executable steps were supplied")
    if any(not _safe_step(step) for step in action.steps):
        return ExecutionDecision(False, "one or more steps are outside the safe execution allowlist")
    if any(_step_operation(step) is None for step in action.steps):
        return ExecutionDecision(False, "one or more steps cannot be mapped to a fixed sandbox operation")
    granted = [Capability.INSPECT, Capability.TEST, Capability.LINT, Capability.METRICS, Capability.READ_FILE, Capability.BENCHMARK]
    for step in action.steps:
        operation = _step_operation(step)
        assert operation is not None
        try:
            capability = Capability(operation[0])
        except ValueError:
            return ExecutionDecision(False, "sandbox operation has no registered capability")
        decision = check_capability(capability, granted)
        if not decision.allowed:
            return ExecutionDecision(False, decision.reason)
    return ExecutionDecision(True, "approved action is limited to explicitly granted safe capabilities")


def execute_approved_action(action: PendingAction, approval: ApprovalRecord, root: Path, now: datetime | None = None, audit_path: Path | None = None, claim_store: Path | None = None, lifecycle_path: Path | None = None) -> ExecutionDecision:
    decision = authorize_execution(action, approval, now, audit_path)
    if not decision.allowed:
        return decision
    if not root.exists() or not root.is_dir():
        return ExecutionDecision(False, "execution root is not a valid project directory")
    if claim_store is None:
        return ExecutionDecision(False, "approval consumption store is required")
    if lifecycle_path is None:
        return ExecutionDecision(False, "lifecycle ledger is required")
    trusted, reason = require_state(lifecycle_path, action.id, LifecycleState.APPROVED)
    if not trusted:
        return ExecutionDecision(False, reason)

    # Persist the lifecycle transition before consuming the approval claim. If a
    # process crashes after this point, recovery sees CLAIMED and can safely
    # terminally block instead of replaying the action.
    persisted, transition_reason = record_transition(
        lifecycle_path,
        action.id,
        LifecycleState.APPROVED,
        LifecycleState.CLAIMED,
    )
    if not persisted:
        return ExecutionDecision(False, transition_reason)

    claimed = claim_approval(approval, claim_store)
    if not claimed.allowed:
        record_transition(lifecycle_path, action.id, LifecycleState.CLAIMED, LifecycleState.BLOCKED)
        return claimed

    records: list[ExecutionRecord] = []
    approval_id = approval_claim_id(approval)
    for step in action.steps:
        operation = _step_operation(step)
        if operation is None:
            record_transition(lifecycle_path, action.id, LifecycleState.CLAIMED, LifecycleState.BLOCKED)
            return ExecutionDecision(False, "sandbox operation mapping failed", tuple(records))
        op, target = operation
        result = run_safe_operation(op, root, target)
        records.append(to_execution_record(action.id, approval_id, result))
        if not result.success:
            record_transition(lifecycle_path, action.id, LifecycleState.CLAIMED, LifecycleState.BLOCKED)
            return ExecutionDecision(False, f"sandbox operation '{op}' failed; progression stopped", tuple(records))

    persisted, transition_reason = record_transition(
        lifecycle_path,
        action.id,
        LifecycleState.CLAIMED,
        LifecycleState.EXECUTED,
    )
    if not persisted:
        return ExecutionDecision(False, transition_reason, tuple(records))
    return ExecutionDecision(True, "approved action executed through the safe sandbox", tuple(records))
