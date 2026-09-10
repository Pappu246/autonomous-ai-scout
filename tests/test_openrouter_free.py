from autonomous_agent.openrouter_free import OPENROUTER_FREE_MODEL, chat_free


def test_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = chat_free("hello")
    assert not result.attempted and not result.success


def test_uses_only_free_router(monkeypatch):
    captured = {}
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}
    def fake_post(url, headers, json, timeout):
        captured.update(url=url, json=json)
        return FakeResponse()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("autonomous_agent.openrouter_free.httpx.post", fake_post)
    result = chat_free("hello")
    assert result.success and result.text == "ok"
    assert captured["json"]["model"] == OPENROUTER_FREE_MODEL


def test_no_paid_retry_on_429(monkeypatch):
    class FakeResponse:
        status_code = 429
        def json(self): return {}
    calls = []
    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"]["model"])
        return FakeResponse()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("autonomous_agent.openrouter_free.httpx.post", fake_post)
    result = chat_free("hello")
    assert result.attempted and not result.success
    assert calls == ["openrouter/free"]
