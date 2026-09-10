from __future__ import annotations

from autonomous_agent.openrouter_free import OPENROUTER_FREE_MODEL, chat_free


def test_openrouter_free_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = chat_free("hello")
    assert not result.attempted
    assert not result.success


def test_openrouter_adapter_uses_only_free_router(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("autonomous_agent.openrouter_free.httpx.post", fake_post)

    result = chat_free("hello")
    assert result.success
    assert result.text == "ok"
    assert captured["json"]["model"] == OPENROUTER_FREE_MODEL
    assert captured["json"]["model"] == "openrouter/free"


def test_openrouter_adapter_never_retries_paid_on_rate_limit(monkeypatch):
    class FakeResponse:
        status_code = 429

        def json(self):
            return {}

    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"]["model"])
        return FakeResponse()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("autonomous_agent.openrouter_free.httpx.post", fake_post)

    result = chat_free("hello")
    assert result.attempted
    assert not result.success
    assert calls == ["openrouter/free"]
    assert "no paid retry" in result.detail
