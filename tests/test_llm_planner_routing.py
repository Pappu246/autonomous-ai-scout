from __future__ import annotations

from autonomous_agent.llm_planner import plan_with_free_llm
from autonomous_agent.models import AccessStatus, ModelCandidate


def test_planner_uses_openrouter_when_no_gemini_candidate(monkeypatch):
    monkeypatch.setattr(
        "autonomous_agent.llm_planner._plan_with_openrouter",
        lambda task: __import__("autonomous_agent.llm_planner", fromlist=["LLMPlan"]).LLMPlan(
            task, "openrouter/free", "openrouter", "ok", ("inspect",), False
        ),
    )
    result = plan_with_free_llm("inspect the project", [])
    assert result is not None
    assert result.provider == "openrouter"
    assert result.model == "openrouter/free"


def test_planner_falls_back_to_openrouter_when_gemini_is_unavailable(monkeypatch):
    candidate = ModelCandidate(
        provider="gemini",
        model="gemini-3.7-flash",
        source_url="https://example.com",
        access_status=AccessStatus.VERIFIED_FREE,
    )
    monkeypatch.setattr("autonomous_agent.llm_planner._plan_with_gemini", lambda task, model: None)
    monkeypatch.setattr(
        "autonomous_agent.llm_planner._plan_with_openrouter",
        lambda task: __import__("autonomous_agent.llm_planner", fromlist=["LLMPlan"]).LLMPlan(
            task, "openrouter/free", "openrouter", "fallback", ("inspect",), False
        ),
    )
    result = plan_with_free_llm("inspect the project", [candidate])
    assert result is not None
    assert result.provider == "openrouter"
    assert result.model == "openrouter/free"


def test_planner_never_uses_unverified_candidate():
    candidate = ModelCandidate(
        provider="gemini",
        model="gemini-3.7-flash",
        source_url="https://example.com",
        access_status=AccessStatus.PAID_ONLY,
    )
    called = []
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr("autonomous_agent.llm_planner._plan_with_gemini", lambda *args: called.append(args) or None)
    monkeypatch.setattr("autonomous_agent.llm_planner._plan_with_openrouter", lambda task: None)
    try:
        assert plan_with_free_llm("inspect", [candidate]) is None
        assert called == []
    finally:
        monkeypatch.undo()
