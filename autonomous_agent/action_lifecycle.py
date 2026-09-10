from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .action_queue import PendingAction


class LifecycleState(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    TESTED = "tested"
    SECURED = "secured"
    POLICY_CHECKED = "policy_checked"
    APPROVED = "approved"
    CLAIMED = "claimed"
    EXECUTED = "executed"
    VERIFIED = "verified"
    BLOCKED = "blocked"
    COMPLETED = "completed"


_TERMINAL = frozenset({LifecycleState.BLOCKED, LifecycleState.COMPLETED})
_TRANSITIONS: Mapping[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.PROPOSED: frozenset({LifecycleState.VALIDATED, LifecycleState.BLOCKED}),
    LifecycleState.VALIDATED: frozenset({LifecycleState.TESTED, LifecycleState.BLOCKED}),
    LifecycleState.TESTED: frozenset({LifecycleState.SECURED, LifecycleState.BLOCKED}),
    LifecycleState.SECURED: frozenset({LifecycleState.POLICY_CHECKED, LifecycleState.BLOCKED}),
    LifecycleState.POLICY_CHECKED: frozenset({LifecycleState.APPROVED, LifecycleState.BLOCKED}),
    LifecycleState.APPROVED: frozenset({LifecycleState.CLAIMED, LifecycleState.BLOCKED}),
    LifecycleState.CLAIMED: frozenset({LifecycleState.EXECUTED, LifecycleState.BLOCKED}),
    LifecycleState.EXECUTED: frozenset({LifecycleState.VERIFIED, LifecycleState.BLOCKED}),
    LifecycleState.VERIFIED: frozenset({LifecycleState.COMPLETED, LifecycleState.BLOCKED}),
    LifecycleState.BLOCKED: frozenset(),
    LifecycleState.COMPLETED: frozenset(),
}


@dataclass(frozen=True)
class LifecycleDecision:
    allowed: bool
    reason: str
    state: LifecycleState


def can_transition(current: LifecycleState | str, target: LifecycleState | str) -> LifecycleDecision:
    try:
        current_state = LifecycleState(current)
        target_state = LifecycleState(target)
    except (TypeError, ValueError):
        return LifecycleDecision(False, "lifecycle state is invalid", LifecycleState.BLOCKED)
    if current_state in _TERMINAL:
        return LifecycleDecision(False, "lifecycle is already terminal", current_state)
    if target_state not in _TRANSITIONS[current_state]:
        return LifecycleDecision(False, f"invalid lifecycle transition: {current_state.value} -> {target_state.value}", current_state)
    return LifecycleDecision(True, "lifecycle transition is allowed", target_state)


def advance(state: LifecycleState | str, target: LifecycleState | str) -> LifecycleState:
    decision = can_transition(state, target)
    if not decision.allowed:
        raise ValueError(decision.reason)
    return decision.state


def initial_state(action: PendingAction) -> LifecycleDecision:
    if not isinstance(action, PendingAction):
        return LifecycleDecision(False, "action is invalid", LifecycleState.BLOCKED)
    if not action.id.strip() or not action.task.strip() or not action.steps:
        return LifecycleDecision(False, "action is incomplete", LifecycleState.BLOCKED)
    if action.status not in {"pending", "approved"}:
        return LifecycleDecision(False, "action status is outside the lifecycle entry boundary", LifecycleState.BLOCKED)
    return LifecycleDecision(True, "action entered lifecycle at proposed state", LifecycleState.PROPOSED)


def authorize_lifecycle_completion(states: list[LifecycleState | str], *, approval_present: bool, claim_present: bool, post_change_verified: bool) -> LifecycleDecision:
    required = [LifecycleState.PROPOSED, LifecycleState.VALIDATED, LifecycleState.TESTED, LifecycleState.SECURED, LifecycleState.POLICY_CHECKED, LifecycleState.APPROVED, LifecycleState.CLAIMED, LifecycleState.EXECUTED, LifecycleState.VERIFIED]
    try:
        observed = [LifecycleState(item) for item in states]
    except (TypeError, ValueError):
        return LifecycleDecision(False, "lifecycle state sequence is invalid", LifecycleState.BLOCKED)
    if observed != required:
        return LifecycleDecision(False, "lifecycle completion requires the full ordered gate sequence", LifecycleState.BLOCKED)
    if not approval_present:
        return LifecycleDecision(False, "approval is missing", LifecycleState.BLOCKED)
    if not claim_present:
        return LifecycleDecision(False, "approval claim is missing", LifecycleState.BLOCKED)
    if not post_change_verified:
        return LifecycleDecision(False, "post-change verification is missing", LifecycleState.BLOCKED)
    return LifecycleDecision(True, "complete lifecycle is verified; completion is allowed", LifecycleState.COMPLETED)
