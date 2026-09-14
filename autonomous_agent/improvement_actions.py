from __future__ import annotations

from pathlib import Path

from .action_queue import ActionProposal, PendingAction, build_action_proposal, enqueue_proposal
from .continuous_improvement import ImprovementProposal


def proposal_to_action(proposal: ImprovementProposal) -> ActionProposal:
    """Convert a reviewed improvement proposal into the existing approval queue contract."""
    task = f"Improve {proposal.project}: {proposal.problem}. Proposed solution: {proposal.proposed_solution}"
    steps = (
        f"Inspect affected area: {', '.join(proposal.affected_area[:10])}",
        "Prepare a bounded patch proposal and regression tests.",
        "Run the existing validation strategy.",
        "Require the existing approval/change boundary before any source write.",
    )
    return build_action_proposal(task, steps, llm_requires_approval=True)


def enqueue_improvement(path: Path, proposal: ImprovementProposal) -> PendingAction | None:
    """Queue an improvement for approval only; this never executes or creates a PR."""
    action = proposal_to_action(proposal)
    return enqueue_proposal(path, action, risk=proposal.risk.name.lower())
