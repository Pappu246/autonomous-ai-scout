from autonomous_agent.openrouter_runtime import plan_with_openrouter


def test_runtime_rejects_invalid_json(monkeypatch):
    class Result:
        success = True
        text = "not json"
    monkeypatch.setattr("autonomous_agent.openrouter_runtime.chat_free", lambda prompt: Result())
    assert plan_with_openrouter("inspect") is None
