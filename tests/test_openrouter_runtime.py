from autonomous_agent.openrouter_runtime import plan_with_openrouter


def test_openrouter_runtime_returns_bounded_plan(monkeypatch):
    class Result:
        success = True
        text = '{"summary":"inspect project","steps":["read files","run tests","review results"]}'

    monkeypatch.setattr("autonomous_agent.openrouter_runtime.chat_free", lambda prompt: Result())
    result = plan_with_openrouter("inspect project")
    assert result == ("inspect project", ("read files", "run tests", "review results"))


def test_openrouter_runtime_skips_without_task():
    assert plan_with_openrouter("   ") is None
