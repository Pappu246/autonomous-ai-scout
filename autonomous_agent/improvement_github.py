from __future__ import annotations

from .action_queue import PendingAction
from .continuous_improvement import ImprovementProposal
from .github_changes import GitHubChangeRequest, build_change_request


def build_improvement_change_request(
    proposal: ImprovementProposal,
    action: PendingAction,
    *,
    base_branch: str,
    head_branch: str,
    unified_diff: str,
) -> GitHubChangeRequest:
    """Prepare an improvement PR request through the existing review boundary only.

    No branch, commit, PR, merge, or deployment is performed here.
    """
    title = f"fix: {proposal.problem[:80]}"
    body = (
        f"Improvement fingerprint: {proposal.fingerprint}\n\n"
        f"Project: {proposal.project}\n"
        f"Evidence: {proposal.evidence[0].source} ({proposal.evidence[0].fingerprint})\n"
        f"Expected benefit: {proposal.expected_benefit}\n"
        f"Validation: {'; '.join(proposal.validation_strategy)}\n"
        f"Approval: {proposal.approval_requirement}\n"
        "Generated as a reviewable proposal; no automatic merge or deployment."
    )
    return build_change_request(
        action,
        proposal.project,
        base_branch,
        head_branch,
        title,
        body,
        unified_diff,
    )
