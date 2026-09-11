from __future__ import annotations

import hashlib
import json

from .action_queue import ActionProposal, PendingAction
from .continuous_improvement import ImprovementProposal
from .github_changes import GitHubChangeRequest, build_change_request


def _metadata_action(action: ActionProposal) -> PendingAction:
    """Adapt a planner proposal for metadata-only review without mutating the approval queue."""
    payload = json.dumps([action.task, action.steps, "medium"], separators=(",", ":"), sort_keys=True)
    action_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return PendingAction(
        id=action_id,
        task=action.task,
        steps=action.steps,
        risk="medium",
        reason=action.reason,
        status="pending",
        created_at="",
    )


def build_improvement_change_request(
    proposal: ImprovementProposal,
    action: ActionProposal | PendingAction,
    *,
    base_branch: str,
    head_branch: str,
    unified_diff: str,
) -> GitHubChangeRequest:
    """Prepare an improvement PR request through the existing review boundary only.

    ActionProposal input is supported for read-only metadata generation. Actual remote
    mutation must use the PendingAction produced by the existing approval queue.
    """
    pending = action if isinstance(action, PendingAction) else _metadata_action(action)
    title = f"fix: {proposal.problem[:80]}"
    body = (
        f"Improvement fingerprint: {proposal.fingerprint}\n\n"
        f"Project: {proposal.project}\n"
        f"Evidence: {proposal.evidence[0].source} ({proposal.evidence[0].fingerprint})\n"
        f"Expected benefit: {proposal.expected_benefit}\n"
        f"Validation: {'; '.join(proposal.validation_strategy)}\n"
        f"Approval: {proposal.approval_requirement}\n"
        "Generated as a reviewable proposal; protected-branch operations remain gated."
    )
    return build_change_request(pending, proposal.project, base_branch, head_branch, title, body, unified_diff)
