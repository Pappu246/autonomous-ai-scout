from __future__ import annotations

from dataclasses import dataclass

from .models import ProjectFinding


@dataclass(frozen=True)
class ImprovementProposal:
    repository: str
    title: str
    rationale: str
    actions: tuple[str, ...]
    risk: str
    requires_approval: bool = True


def _proposal_for(finding: ProjectFinding) -> ImprovementProposal | None:
    title = finding.title.lower()
    if "hard-coded secret" in title:
        actions = (
            "identify the secret source and affected files",
            "move credentials to environment/secret storage",
            "add a regression check for secret-pattern detection",
        )
        return ImprovementProposal(finding.repository, "Remove hard-coded secret risk", finding.detail, actions, "high")
    if "no lockfile" in title:
        actions = (
            "identify the package manager",
            "generate a lockfile using the existing manifest",
            "run the test suite and dependency audit",
        )
        return ImprovementProposal(finding.repository, "Add reproducible dependency locking", finding.detail, actions, "medium")
    if "not fully version-constrained" in title:
        actions = (
            "identify unconstrained dependencies",
            "pin them to reviewed compatible versions",
            "run tests and dependency security checks",
        )
        return ImprovementProposal(finding.repository, "Pin unconstrained dependencies", finding.detail, actions, "medium")
    if "lifecycle scripts" in title:
        actions = (
            "review install lifecycle scripts",
            "remove or minimize unnecessary install-time execution",
            "run installation and tests in a clean environment",
        )
        return ImprovementProposal(finding.repository, "Reduce dependency install-hook risk", finding.detail, actions, "medium")
    if "license" in title:
        return ImprovementProposal(
            finding.repository,
            "Add an explicit open-source license",
            finding.detail,
            ("confirm the intended license", "add the license file", "verify repository metadata"),
            "medium",
        )
    if "bug" in title or "issue backlog" in title:
        return ImprovementProposal(
            finding.repository,
            "Triage outstanding project issues",
            finding.detail,
            ("group open issues by severity", "select a bounded fix batch", "add tests before implementation"),
            "medium",
        )
    return None


def build_improvement_proposals(findings: list[ProjectFinding], limit: int = 10) -> list[ImprovementProposal]:
    """Convert actionable findings into bounded, approval-gated improvement proposals."""
    limit = max(0, min(int(limit), 20))
    proposals: list[ImprovementProposal] = []
    seen: set[tuple[str, str]] = set()
    ordered = sorted(findings, key=lambda f: ({"high": 0, "medium": 1, "low": 2, "info": 3}.get(f.severity, 4), f.repository, f.title))
    for finding in ordered:
        proposal = _proposal_for(finding)
        if proposal is None:
            continue
        key = (proposal.repository, proposal.title)
        if key in seen:
            continue
        seen.add(key)
        proposals.append(proposal)
        if len(proposals) >= limit:
            break
    return proposals
