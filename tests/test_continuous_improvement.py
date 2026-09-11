from __future__ import annotations

from pathlib import Path

from autonomous_agent.continuous_improvement import (
    ImprovementRisk,
    OpenChange,
    build_proposal,
    build_report,
    deduplicate_proposals,
    generate_tasks,
    health_trend,
    portfolio,
    prioritize,
    propose_from_source,
)
from autonomous_agent.cross_project_memory import CrossProjectMemory
from autonomous_agent.models import ProjectFinding
from autonomous_agent.task_plan_models import TaskIntent
from autonomous_agent.tool_registry import REGISTRY


class Memory:
    def __init__(self, *, improvements=(), recommendations=()):
        self.improvements = set(improvements)
        self.recommendations = set(recommendations)

    def has(self, *, project, kind, fingerprint):
        return (project, kind, fingerprint) in self.improvements

    def recommendation_needed(self, project, recommendation):
        return (project, recommendation) not in self.recommendations

    def record_recommendation(self, project, recommendation, *, status):
        self.recommendations.add((project, recommendation))
        return True

    def record_improvement(self, project, improvement, *, status):
        self.improvements.add((project, "improvement", improvement))
        return True


class Source:
    def __init__(self):
        self.data = {
            "owner/one": [finding()],
            "owner/new": [finding("medium", "Dependency risk", "Review dependency")],
        }

    def findings(self, project):
        return tuple(self.data.get(project, ()))

    def intelligence(self, project):
        return {"signals": ["validation"], "priorities": ["tests"], "dependencies": ["runtime"]}

    def changes(self, project):
        return ()


def finding(severity="high", title="Open bug regression", recommendation="Add a regression test"):
    return ProjectFinding(repository="owner/repo", severity=severity, title=title, detail="CI failure evidence", recommendation=recommendation, confidence=0.9)


def test_proposal_contains_required_evidence_and_deterministic_fingerprint():
    first = build_proposal("owner/repo", finding(), intelligence={"signals": ["validation"], "priorities": ["tests"]})
    second = build_proposal("owner/repo", finding(), intelligence={"signals": ["validation"], "priorities": ["tests"]})
    assert first.fingerprint == second.fingerprint
    assert first.evidence and first.evidence[0].source == "project_intelligence"
    assert first.validation_strategy
    assert first.rollback_strategy
    assert first.approval_requirement == "human_review_for_source_change"
    assert first.impact_analysis.project == "owner/repo"


def test_critical_security_outranks_cosmetic_work():
    critical = build_proposal("z/project", finding("critical", "Hard-coded secret detected", "Rotate secret"))
    cosmetic = build_proposal("a/project", finding("low", "Documentation gap", "Improve docs"))
    assert critical.risk is ImprovementRisk.CRITICAL
    assert portfolio((cosmetic, critical)) is critical


def test_prioritization_is_deterministic():
    items = [build_proposal("b", finding("medium", "Bug B")), build_proposal("a", finding("medium", "Bug A"))]
    assert [p.project for p in prioritize(items)] == ["a", "b"]
    assert prioritize(items) == prioritize(reversed(items))


def test_open_pr_deduplication_blocks_exact_repeat():
    proposal = build_proposal("owner/repo", finding())
    assert deduplicate_proposals((proposal,), open_changes=(OpenChange("owner/repo", proposal.fingerprint, "same"),)) == ()


def test_memory_deduplication_blocks_previous_improvement():
    proposal = build_proposal("owner/repo", finding())
    memory = Memory(improvements={("owner/repo", "improvement", proposal.fingerprint)})
    assert deduplicate_proposals((proposal,), memory=memory) == ()


def test_memory_recommendation_deduplication_blocks_repeat():
    proposal = build_proposal("owner/repo", finding())
    memory = Memory(recommendations={("owner/repo", proposal.proposed_solution)})
    assert deduplicate_proposals((proposal,), memory=memory) == ()


def test_recent_finding_deduplication_blocks_only_same_problem():
    proposal = build_proposal("owner/repo", finding())
    other = build_proposal("owner/repo", finding(title="Different bug"))
    result = deduplicate_proposals((proposal, other), recent_findings=(proposal.problem,))
    assert proposal not in result
    assert other in result


def test_cross_project_isolation_keeps_same_finding_in_separate_projects():
    first = build_proposal("owner/one", finding())
    second = build_proposal("owner/two", finding())
    assert first.fingerprint != second.fingerprint
    assert len(deduplicate_proposals((first, second))) == 2


def test_changed_evidence_creates_new_fingerprint():
    first = build_proposal("owner/repo", finding(recommendation="Add regression test"))
    second = build_proposal("owner/repo", finding(recommendation="Fix failing CI and add regression test"))
    assert first.fingerprint != second.fingerprint


def test_health_trend():
    assert health_trend({"score": 80}, {"score": 70}) == "deteriorated"
    assert health_trend({"score": 70}, {"score": 80}) == "improved"
    assert health_trend({"score": 70}, {"score": 70}) == "unchanged"


def test_task_generation_only_prepares_existing_plans():
    proposal = build_proposal("owner/repo", finding("medium", "Test weakness", "Add tests"))
    generated = generate_tasks((proposal,), registry=REGISTRY)
    assert len(generated) == 1
    assert generated[0][1].intent is TaskIntent.TEST
    assert generated[0][1].executable is False
    assert generated[0][1].steps[-1].tool_name == "tests.run"


def test_report_suppresses_empty_no_change():
    assert build_report([])["meaningful_change"] is False
    assert build_report([], blocked_actions=())["meaningful_change"] is False


def test_report_contains_only_safe_identifiers_for_proposals():
    proposal = build_proposal("owner/repo", finding())
    report = build_report((proposal,))
    assert report["highest_priority"][0]["fingerprint"] == proposal.fingerprint
    assert "CI failure evidence" not in str(report)
    assert proposal.fingerprint in report["required_approvals"]


def test_secret_like_evidence_is_redacted_from_proposal():
    proposal = build_proposal("owner/repo", ProjectFinding(repository="owner/repo", severity="high", title="Secret issue", detail="API key=supersecret", recommendation="Rotate credentials", confidence=0.8))
    assert proposal.fingerprint
    assert "supersecret" not in proposal.fingerprint
    assert "supersecret" not in proposal.evidence[0].summary
    assert "supersecret" not in proposal.proposed_solution


def test_risk_and_approval_are_explicit_for_source_changes():
    proposal = build_proposal("owner/repo", finding("high", "Bug fix", "Modify source"))
    assert proposal.risk in {ImprovementRisk.HIGH, ImprovementRisk.CRITICAL}
    assert proposal.approval_requirement == "human_review_for_source_change"


def test_future_registry_project_is_evaluated_without_engine_changes():
    proposals = propose_from_source(Source(), ("owner/one", "owner/new"))
    assert {item.project for item in proposals} == {"owner/one", "owner/new"}


def test_repeated_failure_is_learned_and_deduplicated(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    proposal = build_proposal("owner/repo", finding())
    assert memory.record_improvement("owner/repo", proposal.fingerprint, status="rejected") is True
    assert deduplicate_proposals((proposal,), memory=memory) == ()
    changed = build_proposal("owner/repo", finding(recommendation="Fix the failure and add regression coverage"))
    assert changed.fingerprint != proposal.fingerprint


def test_accepted_and_rejected_learning_remain_project_scoped(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    first = build_proposal("owner/one", finding())
    second = build_proposal("owner/two", finding())
    assert memory.record_improvement(first.project, first.fingerprint, status="accepted") is True
    assert memory.record_improvement(second.project, second.fingerprint, status="rejected") is True
    assert len(memory.learn("owner/one", kind="improvement")) == 1
    assert len(memory.learn("owner/two", kind="improvement")) == 1
