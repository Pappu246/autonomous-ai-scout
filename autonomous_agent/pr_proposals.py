from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .improvement_engine import ImprovementProposal


@dataclass(frozen=True)
class PRProposal:
    id: str
    repository: str
    title: str
    body: str
    steps: tuple[str, ...]
    test_command: str = "python -m pytest -q"
    status: str = "proposed"
    requires_approval: bool = True


def build_pr_proposal(proposal: ImprovementProposal) -> PRProposal:
    """Create review-ready PR metadata; never creates a branch, commits, or opens a PR."""
    if not proposal.requires_approval:
        raise ValueError("PR proposals must remain approval-gated")
    normalized = (proposal.repository.strip(), proposal.title.strip(), proposal.risk.lower())
    raw = json.dumps([normalized, proposal.actions], separators=(",", ":"), sort_keys=True)
    proposal_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    body = (
        f"## Proposed improvement\n\n{proposal.rationale}\n\n"
        "### Planned steps\n" + "\n".join(f"- {step}" for step in proposal.actions) +
        f"\n\n**Risk:** {proposal.risk}\n**Approval required:** yes\n"
        "\nNo branch, commit, merge, or deployment is created automatically."
    )
    return PRProposal(proposal_id, proposal.repository, proposal.title, body, proposal.actions)


def save_pr_proposals(path: Path, proposals: list[PRProposal], limit: int = 20) -> None:
    """Persist bounded PR proposal metadata only."""
    bounded = proposals[: max(0, min(int(limit), 20))]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(item) for item in bounded], indent=2) + "\n", encoding="utf-8")
