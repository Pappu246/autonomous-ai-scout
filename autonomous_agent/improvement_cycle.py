from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .continuous_improvement import OpenChange, build_report, propose_from_source, prioritize
from .cross_project_memory import CrossProjectMemory
from .github_audit import audit_owner, _load_state
from .models import ProjectFinding


REPORT_PATH = Path(os.getenv("SCOUT_IMPROVEMENT_REPORT_PATH", "state/improvement_report.json"))


class AuditEvidenceSource:
    """Read-only bridge from the existing GitHub audit/intelligence pipeline into Phase K."""

    def __init__(self, owner: str, *, exclude: set[str] | None = None):
        self.owner = owner
        self.exclude = exclude or set()
        self._findings: dict[str, list[ProjectFinding]] = {}
        self._intelligence: dict[str, dict[str, Any]] = {}

    def refresh(self) -> tuple[str, ...]:
        raw = audit_owner(self.owner, exclude=self.exclude)
        for item in raw:
            repository = str(item.get("repository", "")).strip()
            if not repository:
                continue
            finding = ProjectFinding(
                repository=repository,
                severity=str(item.get("severity", "info")),
                title=str(item.get("title", "GitHub audit finding")),
                detail=str(item.get("detail", "")),
                recommendation=str(item.get("recommendation", "Review the finding.")),
                confidence=0.85,
            )
            self._findings.setdefault(repository, []).append(finding)
        intelligence = _load_state(Path(os.getenv("SCOUT_PROJECT_INTELLIGENCE_PATH", "state/project_intelligence.json")))
        self._intelligence = {str(name): dict(value) for name, value in intelligence.items() if isinstance(value, dict)}
        registry = _load_state(Path(os.getenv("SCOUT_PROJECT_REGISTRY_PATH", "state/project_registry.json")))
        return tuple(sorted(name for name, profile in registry.items() if isinstance(profile, dict) and not profile.get("fork") and not profile.get("archived") and name not in self.exclude))

    def findings(self, project: str):
        return tuple(self._findings.get(project, ()))

    def intelligence(self, project: str):
        return self._intelligence.get(project, {})

    def changes(self, project: str):
        return ()


def _open_changes(projects: tuple[str, ...]) -> tuple[OpenChange, ...]:
    """Reuse the existing read-only GitHub client to deduplicate against open PRs."""
    from .github_audit import gh_get

    result: list[OpenChange] = []
    for project in projects:
        pulls = gh_get(f"/repos/{project}/pulls", {"state": "open", "per_page": 100, "sort": "updated"})
        if not isinstance(pulls, list):
            continue
        for pull in pulls:
            body = str(pull.get("body") or "")
            marker = "fingerprint:"
            fingerprints = [line.split(marker, 1)[1].strip().split()[0] for line in body.splitlines() if marker in line.lower()]
            for fingerprint in fingerprints:
                if fingerprint:
                    result.append(OpenChange(project, fingerprint, str(pull.get("title", "open pull request"))))
    return tuple(result)


def run_improvement_cycle(owner: str, *, memory_path: Path | None = None, exclude: set[str] | None = None) -> dict[str, object]:
    """Discover all owned projects, evaluate existing evidence, and persist a safe decision report."""
    source = AuditEvidenceSource(owner, exclude=exclude)
    projects = source.refresh()
    memory = CrossProjectMemory(memory_path or Path(os.getenv("SCOUT_MEMORY_PATH", "state/cross_project_memory.json")))
    proposals = propose_from_source(source, projects, memory=memory, open_changes=_open_changes(projects))
    ordered = prioritize(proposals)
    for proposal in ordered:
        try:
            memory.record_recommendation(proposal.project, proposal.proposed_solution, status="proposed")
        except Exception:
            pass
    report = build_report(ordered, blocked_actions=("github.change", "github.merge", "production.deploy", "billing.manage", "secrets.manage", "destructive.execute"))
    report["owner"] = owner
    report["projects_evaluated"] = projects
    report["proposal_count"] = len(ordered)
    report["top_proposal"] = None if not ordered else {
        "project": ordered[0].project,
        "problem": ordered[0].problem,
        "score": ordered[0].priority_score,
        "fingerprint": ordered[0].fingerprint,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT_PATH.with_suffix(REPORT_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(REPORT_PATH)
    return report
