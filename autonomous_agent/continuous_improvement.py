from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Mapping, Protocol

from .models import ProjectFinding
from .task_planner import plan_task
from .tool_registry import ToolRegistry, REGISTRY


class ImprovementRisk(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


_SEVERITY_WEIGHT = {"info": 5, "low": 20, "medium": 50, "high": 80, "critical": 100, "warning": 65}
_SECRET = re.compile(r"(?i)(?:api[_-]?key|api\s+key|access[_-]?token|access\s+token|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----.*?-----END [A-Z0-9 ]+PRIVATE KEY-----", re.S)


@dataclass(frozen=True)
class ImprovementEvidence:
    source: str
    summary: str
    confidence: float
    fingerprint: str


@dataclass(frozen=True)
class ImpactAnalysis:
    project: str
    affected_components: tuple[str, ...]
    dependencies: tuple[str, ...]
    regression_surface: tuple[str, ...]
    required_tests: tuple[str, ...]
    side_effects: tuple[str, ...]


@dataclass(frozen=True)
class ImprovementProposal:
    project: str
    problem: str
    evidence: tuple[ImprovementEvidence, ...]
    proposed_solution: str
    expected_benefit: str
    affected_area: tuple[str, ...]
    confidence: float
    risk: ImprovementRisk
    effort: int
    severity: int
    urgency: int
    impact: int
    regression_risk: int
    project_importance: int
    security_impact: int
    reliability_impact: int
    validation_strategy: tuple[str, ...]
    rollback_strategy: str
    approval_requirement: str
    fingerprint: str
    impact_analysis: ImpactAnalysis

    @property
    def priority_score(self) -> int:
        raw = self.severity * 3 + self.impact * 2 + round(self.confidence * 100) + self.urgency * 2 + self.project_importance + self.security_impact * 2 + self.reliability_impact * 2 - self.effort - self.regression_risk - self.risk * 3
        return max(0, raw)


@dataclass(frozen=True)
class OpenChange:
    project: str
    fingerprint: str
    title: str


class EvidenceMemory(Protocol):
    def has(self, *, project: str, kind: str, fingerprint: str) -> bool: ...
    def recommendation_needed(self, project: str, recommendation: str) -> bool: ...
    def record_recommendation(self, project: str, recommendation: str, *, status: str) -> bool: ...
    def record_improvement(self, project: str, improvement: str, *, status: str) -> bool: ...


class ProjectEvidenceSource(Protocol):
    def findings(self, project: str) -> Iterable[ProjectFinding]: ...
    def intelligence(self, project: str) -> Mapping[str, object]: ...
    def changes(self, project: str) -> Iterable[Mapping[str, object]]: ...


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_text(value: object) -> str:
    text = str(value)
    text = _PRIVATE_KEY.sub("[REDACTED]", text)
    return _SECRET.sub("[REDACTED]", text)[:512]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _risk_for(severity: int, security: int, reliability: int) -> ImprovementRisk:
    if severity >= 90 or security >= 90:
        return ImprovementRisk.CRITICAL
    if severity >= 75 or security >= 70 or reliability >= 80:
        return ImprovementRisk.HIGH
    if severity >= 45:
        return ImprovementRisk.MEDIUM
    return ImprovementRisk.LOW


def _classify_finding(finding: ProjectFinding) -> tuple[int, int, int, ImprovementRisk]:
    title = f"{finding.title} {finding.detail}".lower()
    severity = _SEVERITY_WEIGHT.get(finding.severity.lower(), 50)
    security = 100 if any(term in title for term in ("secret", "security", "credential", "vulnerability", "cve")) else 0
    reliability = 90 if any(term in title for term in ("ci", "regression", "failure", "bug", "test")) else 40
    return severity, security, reliability, _risk_for(severity, security, reliability)


def analyze_impact(project: str, finding: ProjectFinding, intelligence: Mapping[str, object]) -> ImpactAnalysis:
    signals = intelligence.get("signals", ())
    priorities = intelligence.get("priorities", ())
    affected = tuple(_safe_text(item) for item in (priorities or signals or (finding.title,)))[:10]
    dependencies = tuple(_safe_text(item) for item in intelligence.get("dependencies", ()))[:20]
    tests = (f"Add or update regression coverage for: {_safe_text(finding.title)}",)
    if any(term in finding.title.lower() for term in ("test", "ci", "regression")):
        tests = ("Run the complete existing test suite.", "Add a focused regression test for the observed failure.")
    return ImpactAnalysis(project=project, affected_components=affected or (_safe_text(finding.title),), dependencies=dependencies, regression_surface=(_safe_text(finding.title), *affected[:4]), required_tests=tests, side_effects=("No production mutation during proposal generation.", "Any source write remains behind the existing approval-gated change boundary."))


def build_proposal(project: str, finding: ProjectFinding, *, intelligence: Mapping[str, object] | None = None, project_importance: int = 50) -> ImprovementProposal:
    intelligence = intelligence or {}
    severity, security, reliability, risk = _classify_finding(finding)
    confidence = _clamp(finding.confidence)
    safe_detail, safe_title, safe_solution = _safe_text(finding.detail), _safe_text(finding.title), _safe_text(finding.recommendation)
    evidence = (ImprovementEvidence("project_intelligence", safe_detail, confidence, _digest((project, safe_title, safe_detail))),)
    impact = min(100, max(severity, 60 + security // 2 + reliability // 4))
    urgency = 100 if severity >= 90 else 80 if severity >= 75 else 50 if severity >= 45 else 20
    effort = 20 if severity >= 75 else 35 if severity >= 45 else 50
    impact_analysis = analyze_impact(project, finding, intelligence)
    fingerprint = _digest({"project": project, "problem": safe_title, "recommendation": safe_solution, "evidence": [item.fingerprint for item in evidence]})
    return ImprovementProposal(project=_safe_text(project), problem=safe_title, evidence=evidence, proposed_solution=safe_solution, expected_benefit=f"Reduce the observed {finding.severity} risk and improve project health without weakening existing controls.", affected_area=impact_analysis.affected_components, confidence=confidence, risk=risk, effort=effort, severity=severity, urgency=urgency, impact=impact, regression_risk=25 if risk >= ImprovementRisk.HIGH else 10, project_importance=max(0, min(100, int(project_importance))), security_impact=security, reliability_impact=reliability, validation_strategy=impact_analysis.required_tests + ("Run existing deterministic project-intelligence checks again.",), rollback_strategy="Revert the reviewed change/PR; do not merge or deploy automatically.", approval_requirement="human_review_for_source_change", fingerprint=fingerprint, impact_analysis=impact_analysis)


def deduplicate_proposals(proposals: Iterable[ImprovementProposal], *, memory: EvidenceMemory | None = None, open_changes: Iterable[OpenChange] = (), recent_findings: Iterable[str] = ()) -> tuple[ImprovementProposal, ...]:
    open_set = {(item.project, item.fingerprint) for item in open_changes}
    recent = set(recent_findings)
    seen: set[str] = set()
    result: list[ImprovementProposal] = []
    for proposal in proposals:
        if proposal.fingerprint in seen or (proposal.project, proposal.fingerprint) in open_set or proposal.problem in recent:
            continue
        if memory is not None and (memory.has(project=proposal.project, kind="improvement", fingerprint=proposal.fingerprint) or not memory.recommendation_needed(proposal.project, proposal.proposed_solution)):
            continue
        seen.add(proposal.fingerprint)
        result.append(proposal)
    return tuple(result)


def prioritize(proposals: Iterable[ImprovementProposal]) -> tuple[ImprovementProposal, ...]:
    return tuple(sorted(proposals, key=lambda item: (-item.priority_score, -item.severity, -item.security_impact, -item.reliability_impact, item.project, item.fingerprint)))


def generate_tasks(proposals: Iterable[ImprovementProposal], *, registry: ToolRegistry = REGISTRY) -> tuple[tuple[ImprovementProposal, object], ...]:
    tasks: list[tuple[ImprovementProposal, object]] = []
    for proposal in prioritize(proposals):
        task = f"Improve {proposal.project}: {proposal.problem}. Proposed solution: {proposal.proposed_solution}"
        tasks.append((proposal, plan_task(task, registry=registry)))
    return tuple(tasks)


def portfolio(proposals: Iterable[ImprovementProposal]) -> ImprovementProposal | None:
    ordered = prioritize(proposals)
    return ordered[0] if ordered else None


def health_trend(previous: Mapping[str, object], current: Mapping[str, object]) -> str:
    old, new = float(previous.get("score", 0)), float(current.get("score", 0))
    return "deteriorated" if new < old else "improved" if new > old else "unchanged"


def build_report(proposals: Iterable[ImprovementProposal], *, blocked_actions: Iterable[str] = (), health_changes: Mapping[str, str] | None = None) -> dict[str, object]:
    ordered = prioritize(proposals)
    approvals = tuple(p.fingerprint for p in ordered if p.approval_requirement != "none")
    blocked = tuple(blocked_actions)
    return {"highest_priority": tuple({"project": p.project, "problem": p.problem, "score": p.priority_score, "fingerprint": p.fingerprint} for p in ordered[:10]), "newly_detected": tuple(p.problem for p in ordered), "proposed_fixes": tuple(p.fingerprint for p in ordered), "blocked_actions": blocked, "required_approvals": approvals, "health_changes": dict(sorted((health_changes or {}).items())), "meaningful_change": bool(ordered or blocked or health_changes)}


def propose_from_source(source: ProjectEvidenceSource, projects: Iterable[str], *, memory: EvidenceMemory | None = None, open_changes: Iterable[OpenChange] = (), project_importance: Mapping[str, int] | None = None) -> tuple[ImprovementProposal, ...]:
    importance = project_importance or {}
    proposals: list[ImprovementProposal] = []
    for project in sorted(set(projects)):
        intelligence = source.intelligence(project)
        for finding in source.findings(project):
            proposals.append(build_proposal(project, finding, intelligence=intelligence, project_importance=importance.get(project, 50)))
    return deduplicate_proposals(proposals, memory=memory, open_changes=open_changes)
