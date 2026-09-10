from __future__ import annotations

import json
from pathlib import Path

from autonomous_agent.benchmark import benchmark_groq
from autonomous_agent.dependency_security import analyze_dependencies
from autonomous_agent.models import AccessStatus, ModelCandidate, Opportunity
from autonomous_agent.opportunities import score_opportunity
from autonomous_agent.opportunity_history import trend_notes, update_history
from autonomous_agent.project_intelligence import analyze_project
from autonomous_agent.release_discovery import discover_releases
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
