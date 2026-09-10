from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autonomous_agent.action_queue import PendingAction, build_action_proposal, enqueue_proposal, load_queue, prioritize_queue
from autonomous_agent.approved_executor import ApprovalRecord, authorize_execution, execute_approved_action
from autonomous_agent.benchmark import BenchmarkResult, benchmark_groq
from autonomous_agent.dependency_security import analyze_dependencies
from autonomous_agent.evaluation import rank_benchmarks, score_benchmark
from autonomous_agent.improvement_engine import ImprovementProposal, build_improvement_proposals
from autonomous_agent.models import AccessStatus, ModelCandidate, Opportunity, ProjectFinding
from autonomous_agent.opportunities import score_opportunity
from autonomous_agent.opportunity_history import trend_notes, update_history
from autonomous_agent.pr_proposals import build_pr_proposal, save_pr_proposals
from autonomous_agent.project_intelligence import analyze_project
from autonomous_agent.release_discovery import discover_releases
from autonomous_agent.router import choose_model
from autonomous_agent.sources import SourceCheck, source_has_free_signal
from autonomous_agent.task_engine import TaskIntent, plan_task
from autonomous_agent.verify import free_candidates
from autonomous_agent.lifecycle_integration import record_transition


def test_free_signal_requires_positive_and_no_negative_signal():
    assert source_has_free_signal(SourceCheck("x", True, text="Free tier and free usage"))
    assert not source_has_free_signal(SourceCheck("x", True, text="No free tier; paid only"))


def test_free_candidates_filters_unknown_and_paid():
    base = dict(provider="x", model="m", source_url="https://example.com")
    values = [
        ModelCandidate(**base, access_status=AccessStatus.VERIFIED_FREE),
        ModelCandidate(**base, access_status=AccessStatus.UNKNOWN),
        ModelCandidate(**base, access_status=AccessStatus.PAID_ONLY),
    ]
    assert len(free_candidates(values)) == 1


def test_opportunity_score_is_bounded():
    assert 0 <= score_opportunity(0, 0, 0) <= 100
    assert score_opportunity(100, 100, 100) == 100


def test_task_planner_understands_common_digital_requests():
    assert plan_task("run tests on the project").intent is TaskIntent.TEST
    assert plan_task("audit my GitHub project").intent is TaskIntent.AUDIT
    assert plan_task("find new free AI models").intent is TaskIntent.DISCOVER
    assert plan_task("fix the bug").requires_approval
    assert plan_task("add a feature").requires_approval


def test_router_never_selects_paid_or_unknown_candidates():
    base = dict(source_url="https://example.com")
    candidates = [
        ModelCandidate(provider="paid", model="best", **base, access_status=AccessStatus.PAID_ONLY),
        ModelCandidate(provider="free", model="gpt-oss-20b", **base, access_status=AccessStatus.VERIFIED_FREE, benchmark_ok=True, benchmark_latency_ms=500),
    ]
    decision = choose_model(candidates, "write code quickly")
    assert decision.model is not None
    assert decision.model.provider == "free"
    assert decision.score > 0


def test_router_prefers_reasoning_fit_when_quality_is_available():
    base = dict(source_url="https://example.com", access_status=AccessStatus.VERIFIED_FREE, benchmark_ok=True, benchmark_latency_ms=1500)
    candidates = [
        ModelCandidate(provider="a", model="generic-model", **base),
        ModelCandidate(provider="b", model="gpt-oss-20b", **base),
    ]
    decision = choose_model(candidates, "analyze this research and reason about the result")
    assert decision.model is not None
    assert decision.model.model == "gpt-oss-20b"
    assert any("reasoning" in reason for reason in decision.reasons)


def test_router_returns_safe_empty_decision_without_free_candidates():
    candidate = ModelCandidate(provider="paid", model="best", source_url="https://example.com", access_status=AccessStatus.PAID_ONLY)
    decision = choose_model([candidate], "anything")
    assert decision.model is None
    assert decision.score == 0.0


def test_router_uses_deterministic_benchmark_score_when_available():
    base = dict(source_url="https://example.com", access_status=AccessStatus.VERIFIED_FREE, benchmark_ok=True, benchmark_latency_ms=500)
    candidates = [
        ModelCandidate(provider="a", model="fast-model", **base, benchmark_score=100),
        ModelCandidate(provider="b", model="gpt-oss-20b", **base, benchmark_score=85),
    ]
    decision = choose_model(candidates, "general task")
    assert decision.model is not None
    assert decision.model.provider == "a"
    assert decision.score == 100
    assert "deterministic benchmark score" in decision.reasons


def test_benchmark_evaluation_scores_success_and_latency():
    result = BenchmarkResult("groq", "openai/gpt-oss-20b", True, True, 500, "ok")
    score = score_benchmark(result)
    assert score.score == 100.0
    assert "benchmark passed" in score.strengths
    assert "low latency" in score.strengths


def test_benchmark_evaluation_penalizes_rate_limit_without_retry():
    result = BenchmarkResult("groq", "openai/gpt-oss-20b", True, False, 6000, "Rate limited; no paid retry attempted.")
    score = score_benchmark(result)
    assert score.score < 50.0
    assert "rate limited" in score.weaknesses
    assert "high latency" in score.weaknesses


def test_benchmark_ranking_is_deterministic():
    results = [
        BenchmarkResult("b", "model-b", True, True, 1200, "ok"),
        BenchmarkResult("a", "model-a", True, True, 500, "ok"),
    ]
    ranked = rank_benchmarks(results)
    assert ranked[0].model == "model-a"


def test_project_intelligence_detects_missing_lockfile(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"dependencies":{"x":"1.0.0"}}', encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('ok')", encoding="utf-8")
    findings = analyze_project(tmp_path, "demo")
    assert any(f.title == "Node project has no lockfile" for f in findings)


def test_project_intelligence_detects_possible_secret(tmp_path: Path):
    (tmp_path / "app.py").write_text("api_key = '12345678901234567890'", encoding="utf-8")
    findings = analyze_project(tmp_path, "demo")
    assert any(f.title == "Possible hard-coded secret" and f.severity == "high" for f in findings)


def test_dependency_security_detects_unpinned_python_dependency(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("httpx\npydantic==2.0.0\n", encoding="utf-8")
    findings = analyze_dependencies(tmp_path, "demo")
    assert any(f.title == "Python dependencies are not fully version-constrained" for f in findings)


def test_dependency_security_detects_node_install_hook(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"scripts":{"postinstall":"node setup.js"},"dependencies":{"x":"1.0.0"}}', encoding="utf-8")
    findings = analyze_dependencies(tmp_path, "demo")
    assert any(f.title == "Node install lifecycle scripts present" and f.severity == "medium" for f in findings)


def test_improvement_engine_generates_bounded_approval_gated_proposals():
    findings = [
        ProjectFinding(repository="demo", severity="medium", title="Node project has no lockfile", detail="missing", recommendation="add one"),
        ProjectFinding(repository="demo", severity="high", title="Possible hard-coded secret", detail="secret-like value", recommendation="move it"),
        ProjectFinding(repository="demo", severity="info", title="Unrelated informational item", detail="none", recommendation="none"),
    ]
    proposals = build_improvement_proposals(findings, limit=1)
    assert len(proposals) == 1
    assert proposals[0].title == "Remove hard-coded secret risk"
    assert proposals[0].risk == "high"
    assert proposals[0].requires_approval is True


def test_improvement_engine_deduplicates_and_caps_output():
    finding = ProjectFinding(repository="demo", severity="medium", title="Node project has no lockfile", detail="missing", recommendation="add one")
    proposals = build_improvement_proposals([finding, finding], limit=20)
    assert len(proposals) == 1


def test_pr_proposal_is_deterministic_and_approval_gated():
    improvement = ImprovementProposal(
        repository="demo",
        title="Add dependency lockfile",
        rationale="Dependencies are not reproducible.",
        actions=("Create the lockfile", "Run the test suite"),
        risk="medium",
        requires_approval=True,
    )
    first = build_pr_proposal(improvement)
    second = build_pr_proposal(improvement)
    assert first.id == second.id
    assert first.requires_approval is True
    assert first.status == "proposed"
    assert "No branch, commit, merge, or deployment" in first.body


def test_pr_proposal_rejects_non_gated_proposal():
    improvement = ImprovementProposal(
        repository="demo",
        title="Safe metadata change",
        rationale="test",
        actions=("update metadata",),
        risk="low",
        requires_approval=False,
    )
    try:
        build_pr_proposal(improvement)
    except ValueError as exc:
        assert "approval-gated" in str(exc)
    else:
        raise AssertionError("non-gated PR proposal was accepted")


def test_pr_proposal_persistence_is_bounded(tmp_path: Path):
    improvement = ImprovementProposal("demo", "Fix", "reason", ("edit",), "low", True)
    proposals = [build_pr_proposal(improvement) for _ in range(30)]
    path = tmp_path / "pr_proposals.json"
    save_pr_proposals(path, proposals, limit=50)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored) <= 20
    assert stored[0]["requires_approval"] is True


def test_approval_queue_deduplicates_identical_pending_actions(tmp_path: Path):
    path = tmp_path / "approval_queue.json"
    proposal = build_action_proposal("fix the bug", ("edit the source",))
    first = enqueue_proposal(path, proposal, "high")
    second = enqueue_proposal(path, proposal, "high")
    assert first is not None and second is not None
    assert first.id == second.id
    assert len(load_queue(path)) == 1
    assert load_queue(path)[0].status == "pending"


def test_approval_queue_prioritizes_risk_without_auto_approval():
    queue = [
        PendingAction("m", "medium", (), "medium", "reason"),
        PendingAction("h", "high", (), "high", "reason"),
        PendingAction("l", "low", (), "low", "reason"),
    ]
    ordered = prioritize_queue(queue)
    assert [item.id for item in ordered] == ["h", "m", "l"]
    assert all(item.status == "pending" for item in ordered)


def test_approval_queue_caps_pending_actions(tmp_path: Path):
    path = tmp_path / "approval_queue.json"
    for i in range(60):
        proposal = build_action_proposal(f"fix bug {i}", ("edit the source",))
        enqueue_proposal(path, proposal, "low")
    queue = load_queue(path)
    assert len(queue) == 50


def test_opportunity_history_records_score_delta_and_caps_length(tmp_path: Path):
    path = tmp_path / "history.json"
    first = [Opportunity(title="A", description="a", score=40, next_step="x")]
    second = [Opportunity(title="A", description="a", score=55, next_step="x")]
    history = update_history(path, first)
    history = update_history(path, second)
    assert history[-1]["opportunities"][0]["score_delta"] == 15
    for score in range(40):
        update_history(path, [Opportunity(title="A", description="a", score=score, next_step="x")])
    assert len(update_history(path, first)) == 30


def test_opportunity_trend_notes_report_changes(tmp_path: Path):
    path = tmp_path / "history.json"
    update_history(path, [Opportunity(title="A", description="a", score=40, next_step="x")])
    history = update_history(path, [Opportunity(title="A", description="a", score=45, next_step="x")])
    assert any("A is up 5 points" in note for note in trend_notes(history))


def test_groq_benchmark_skips_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = benchmark_groq("openai/gpt-oss-20b")
    assert not result.attempted
    assert not result.success
    assert result.latency_ms is None


def test_official_release_discovery_only_uses_enabled_config(monkeypatch):
    def fake_fetch(url: str):
        return SourceCheck(url, True, text="# Release notes\n\n## September 10, 2026\nReleased model x")

    monkeypatch.setattr("autonomous_agent.release_discovery.fetch_source", fake_fetch)
    items = [
        {"id": "gemini", "enabled": True, "release_sources": ["https://official.example/changelog"]},
        {"id": "disabled", "enabled": False, "release_sources": ["https://ignored.example/changelog"]},
    ]
    findings = discover_releases(items, {})
    assert len(findings) == 1
    assert findings[0].provider == "gemini"
    assert not findings[0].changed
    assert "Release notes" in findings[0].headline


def test_official_release_discovery_detects_hash_change(monkeypatch):
    monkeypatch.setattr(
        "autonomous_agent.release_discovery.fetch_source",
        lambda url: SourceCheck(url, True, text="## Release\nnew model"),
    )
    findings = discover_releases(
        [{"id": "groq", "enabled": True, "release_sources": ["https://official.example/changelog"]}],
        {"https://official.example/changelog": "old-hash"},
    )
    assert findings[0].changed


def test_provider_registry_includes_only_explicitly_free_openrouter_router():
    config = json.loads(Path("config/providers.json").read_text(encoding="utf-8"))
    provider = next(item for item in config["providers"] if item["id"] == "openrouter")
    assert provider["enabled"] is True
    assert provider["models"] == ["openrouter/free"]
    assert config["policy"]["free_only"] is True
    assert config["policy"]["never_enable_paid_billing"] is True


def _approved_action(steps=("inspect repository",)):
    return PendingAction("action-123", "Inspect project", tuple(steps), "low", "explicit approval", "approved", datetime.now(timezone.utc).isoformat())


def _approval(action=None, action_id="action-123", expired=False):
    now = datetime.now(timezone.utc)
    if action is not None:
        if expired:
            approved_at = now - timedelta(minutes=2)
            return ApprovalRecord.for_action(action, "user-approved-token", approved_at=approved_at, ttl=timedelta(minutes=1))
        return ApprovalRecord.for_action(action, "user-approved-token", approved_at=now, ttl=timedelta(hours=1))
    expires = now - timedelta(minutes=1) if expired else now + timedelta(hours=1)
    return ApprovalRecord(action_id, now.isoformat(), expires.isoformat(), "user-approved-token")


def _seed_execution_lifecycle(path: Path, action_id: str = "action-123"):
    transitions = [
        ("proposed", "validated"),
        ("validated", "tested"),
        ("tested", "secured"),
        ("secured", "policy_checked"),
        ("policy_checked", "approved"),
    ]
    for current, target in transitions:
        ok, reason = record_transition(path, action_id, current, target)
        assert ok, reason


def test_approved_executor_requires_exact_approval_identity():
    action = _approved_action()
    decision = authorize_execution(action, _approval(action_id="different"))
    assert not decision.allowed
    assert "identity" in decision.reason


def test_approved_executor_rejects_expired_approval():
    action = _approved_action()
    decision = authorize_execution(action, _approval(action=action, expired=True))
    assert not decision.allowed
    assert "expired" in decision.reason


def test_approved_executor_rejects_forbidden_operations():
    action = _approved_action(("deploy production",))
    decision = authorize_execution(action, _approval(action=action))
    assert not decision.allowed
    assert "allowlist" in decision.reason or "forbidden" in decision.reason


def test_approved_executor_requires_consumption_store(tmp_path: Path):
    action = _approved_action()
    decision = execute_approved_action(action, _approval(action=action), tmp_path)
    assert not decision.allowed
    assert "consumption store" in decision.reason


def test_approved_executor_runs_safe_sandbox_operation(tmp_path: Path):
    (tmp_path / "README.txt").write_text("hello", encoding="utf-8")
    action = _approved_action(("inspect repository",))
    lifecycle = tmp_path / "lifecycle.jsonl"
    _seed_execution_lifecycle(lifecycle, action.id)
    decision = execute_approved_action(
        action,
        _approval(action=action),
        tmp_path,
        claim_store=tmp_path / "claims",
        lifecycle_path=lifecycle,
    )
    assert decision.allowed
    assert "sandbox" in decision.reason
    assert len(decision.records) == 1
    assert decision.records[0].category == "inspect"


def test_approved_executor_never_auto_approves(tmp_path: Path):
    action = PendingAction("action-123", "Inspect project", ("inspect repository",), "low", "reason", "pending")
    decision = execute_approved_action(action, _approval(action=action), tmp_path, claim_store=tmp_path / "claims")
    assert not decision.allowed
    assert "not explicitly approved" in decision.reason
