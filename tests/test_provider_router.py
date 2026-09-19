from types import SimpleNamespace

from autonomous_agent.ai_coding_brain import RepositoryContext
from autonomous_agent.coding_provider import ChatProviderConfig
from autonomous_agent.provider_router import (
    CodingProviderRouter,
    ProviderSpec,
    providers_from_env,
)
from autonomous_agent.self_improvement import PatchCandidate


def _proposal():
    return SimpleNamespace(problem="fix", proposed_solution="change", validation_strategy=(), affected_area=())


def _candidate():
    return PatchCandidate("--- a/x\n+++ b/x\n", {"x": "ok"}, "done", ())


class FakeModel:
    def __init__(self, outcomes):
        self.outcomes = outcomes

    def generate_patch(self, **kwargs):
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_router_falls_back_after_provider_failure(monkeypatch):
    monkeypatch.setenv("KEY_A", "secret-a")
    monkeypatch.setenv("KEY_B", "secret-b")
    models = {
        "a": FakeModel([RuntimeError("boom")]),
        "b": FakeModel([_candidate()]),
    }
    specs = (
        ProviderSpec("a", ChatProviderConfig("a", "m", "KEY_A"), priority=1),
        ProviderSpec("b", ChatProviderConfig("b", "m", "KEY_B"), priority=2),
    )
    router = CodingProviderRouter(specs, model_factory=lambda config: models[config.endpoint])
    result = router.generate_patch(proposal=_proposal(), context=RepositoryContext("r", ()))
    assert result == _candidate()
    assert [item.status for item in router.last_attempts] == ["failed", "succeeded"]


def test_router_skips_missing_key_and_paid_route_by_default(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    monkeypatch.setenv("PAID", "secret")
    called = []
    specs = (
        ProviderSpec("missing", ChatProviderConfig("a", "m", "MISSING"), priority=1),
        ProviderSpec("paid", ChatProviderConfig("b", "m", "PAID"), priority=2, cost_class="paid"),
    )
    router = CodingProviderRouter(specs, model_factory=lambda config: called.append(config) or FakeModel([_candidate()]))
    assert router.generate_patch(proposal=_proposal(), context=RepositoryContext("r", ())) is None
    assert called == []
    assert [item.status for item in router.last_attempts] == ["skipped", "skipped"]


def test_router_bounds_retries(monkeypatch):
    monkeypatch.setenv("KEY", "secret")
    model = FakeModel([None, None, _candidate(), _candidate()])
    spec = ProviderSpec("p", ChatProviderConfig("x", "m", "KEY"), max_attempts=99)
    router = CodingProviderRouter((spec,), model_factory=lambda config: model)
    assert router.generate_patch(proposal=_proposal(), context=RepositoryContext("r", ())) == _candidate()
    assert len(router.last_attempts) == 3


def test_providers_from_env_requires_complete_explicit_route(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("CODING_PROVIDER_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODING_PROVIDER_1_NAME", "groq-like")
    monkeypatch.setenv("CODING_PROVIDER_1_ENDPOINT", "https://example.invalid/chat")
    monkeypatch.setenv("CODING_PROVIDER_1_MODEL", "model")
    monkeypatch.setenv("CODING_PROVIDER_1_API_KEY_ENV", "MY_KEY")
    monkeypatch.setenv("CODING_PROVIDER_1_COST_CLASS", "free")
    monkeypatch.setenv("CODING_PROVIDER_1_PRIORITY", "5")
    routes = providers_from_env()
    assert len(routes) == 1
    assert routes[0].name == "groq-like"
    assert routes[0].config.api_key_env == "MY_KEY"
    assert routes[0].cost_class == "free"


def test_attempt_metadata_never_contains_key_value(monkeypatch):
    monkeypatch.setenv("KEY", "super-secret-value")
    spec = ProviderSpec("p", ChatProviderConfig("x", "m", "KEY"))
    router = CodingProviderRouter((spec,), model_factory=lambda config: FakeModel([RuntimeError("super-secret-value")]))
    router.generate_patch(proposal=_proposal(), context=RepositoryContext("r", ()))
    assert "super-secret-value" not in repr(router.last_attempts)
