from __future__ import annotations

from pathlib import Path

from autonomous_agent.models import AccessStatus, ModelCandidate
from autonomous_agent.opportunities import score_opportunity
from autonomous_agent.project_intelligence import analyze_project
from autonomous_agent.router import choose_model
from autonomous_agent.sources import SourceCheck, source_has_free_signal
from autonomous_agent.task_engine import TaskIntent, plan_task
from autonomous_agent.verify import free_candidates


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


def test_project_intelligence_detects_missing_lockfile(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"dependencies":{"x":"1.0.0"}}', encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('ok')", encoding="utf-8")
    findings = analyze_project(tmp_path, "demo")
    assert any(f.title == "Node project has no lockfile" for f in findings)


def test_project_intelligence_detects_possible_secret(tmp_path: Path):
    (tmp_path / "app.py").write_text("api_key = '12345678901234567890'", encoding="utf-8")
    findings = analyze_project(tmp_path, "demo")
    assert any(f.title == "Possible hard-coded secret" and f.severity == "high" for f in findings)
