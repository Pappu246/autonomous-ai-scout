from autonomous_agent.coding_provider import ChatProviderConfig
from autonomous_agent.provider_router import (
    CodingProviderRouter,
    ProviderSpec,
    providers_from_env,
)
from autonomous_agent.self_improvement import PatchCandidate


def _spec(
    name,
    key_env,
    priority=100,
    cost="free",
    max_attempts=1,
):
    return ProviderSpec(
        name=name,
        config=ChatProviderConfig(
            endpoint="https://example.invalid/v1/chat/completions",
            model="demo",
            api_key_env=key_env,
        ),
        priority=priority,
        cost_class=cost,
        max_attempts=max_attempts,
    )


def _proposal():
    return type(
        "Proposal",
        (),
        {
            "problem": "fix bug",
            "proposed_solution": "change code",
            "validation_strategy": ("python -m pytest -q",),
            "affected_area": ("app.py",),
        },
    )()


def _context():
    return type(
        "Context",
        (),
        {
            "repository": "owner/repo",
            "files": (),
        },
    )()


def test_router_skips_unconfigured_and_sorts(monkeypatch):
    monkeypatch.setenv("B_KEY", "x")

    router = CodingProviderRouter(
        [
            _spec("b", "B_KEY", 20),
            _spec("a", "A_KEY", 10),
        ],
        sleep=lambda _: None,
    )

    assert [item.name for item in router.providers] == ["a", "b"]


def test_router_falls_back_after_invalid_candidate(monkeypatch):
    monkeypatch.setenv("A_KEY", "a")
    monkeypatch.setenv("B_KEY", "b")

    class Bad:
        def generate_patch(self, **kwargs):
            return None

    class Good:
        def generate_patch(self, **kwargs):
            return PatchCandidate(
                "diff",
                {"app.py": "print(1)\n"},
                "ok",
                (),
            )

    models = {
        "A_KEY": Bad(),
        "B_KEY": Good(),
    }

    router = CodingProviderRouter(
        [
            _spec("a", "A_KEY", 10),
            _spec("b", "B_KEY", 20),
        ],
        model_factory=lambda config: models[config.api_key_env],
        sleep=lambda _: None,
    )

    result = router.generate_patch(
        proposal=_proposal(),
        context=_context(),
    )

    assert result is not None
    assert result.summary == "ok"
    assert [item.provider for item in router.last_attempts] == ["a", "b"]


def test_router_retries_are_bounded(monkeypatch):
    monkeypatch.setenv("KEY", "x")

    class Flaky:
        def __init__(self):
            self.calls = 0

        def generate_patch(self, **kwargs):
            self.calls += 1
            raise TimeoutError("temporary")

    model = Flaky()

    router = CodingProviderRouter(
        [
            _spec(
                "flaky",
                "KEY",
                max_attempts=99,
            )
        ],
        model_factory=lambda _: model,
        sleep=lambda _: None,
    )

    result = router.generate_patch(
        proposal=_proposal(),
        context=_context(),
    )

    assert result is None
    assert model.calls == 3
    assert len(router.last_attempts) == 3


def test_paid_provider_is_not_selected_by_default(monkeypatch):
    monkeypatch.setenv("PAID_KEY", "secret")

    class Paid:
        def generate_patch(self, **kwargs):
            raise AssertionError("paid provider selected")

    router = CodingProviderRouter(
        [
            _spec(
                "paid",
                "PAID_KEY",
                cost="paid",
            )
        ],
        model_factory=lambda _: Paid(),
        sleep=lambda _: None,
    )

    result = router.generate_patch(
        proposal=_proposal(),
        context=_context(),
    )

    assert result is None
    assert router.last_attempts[0].status == "skipped"


def test_paid_provider_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("PAID_KEY", "secret")

    class Paid:
        def generate_patch(self, **kwargs):
            return PatchCandidate(
                "diff",
                {"app.py": "print(3)\n"},
                "paid",
                (),
            )

    router = CodingProviderRouter(
        [
            _spec(
                "paid",
                "PAID_KEY",
                cost="paid",
            )
        ],
        allow_paid=True,
        model_factory=lambda _: Paid(),
    )

    result = router.generate_patch(
        proposal=_proposal(),
        context=_context(),
    )

    assert result is not None
    assert result.summary == "paid"


def test_provider_env_parser_does_not_store_secret(monkeypatch):
    monkeypatch.setenv("CODING_PROVIDER_1_NAME", "groq")
    monkeypatch.setenv(
        "CODING_PROVIDER_1_ENDPOINT",
        "https://example.invalid",
    )
    monkeypatch.setenv(
        "CODING_PROVIDER_1_MODEL",
        "demo",
    )
    monkeypatch.setenv(
        "CODING_PROVIDER_1_API_KEY_ENV",
        "GROQ_API_KEY",
    )
    monkeypatch.setenv(
        "CODING_PROVIDER_1_COST_CLASS",
        "free",
    )
    monkeypatch.setenv(
        "GROQ_API_KEY",
        "super-secret",
    )

    providers = providers_from_env()

    assert len(providers) == 1
    assert providers[0].config.api_key_env == "GROQ_API_KEY"
    assert "super-secret" not in repr(providers[0])


def test_unknown_cost_class_is_skipped_even_when_key_is_configured(monkeypatch):
    monkeypatch.setenv("UNKNOWN_KEY", "secret")

    class Unknown:
        def generate_patch(self, **kwargs):
            raise AssertionError("unknown-cost provider must never be selected")

    router = CodingProviderRouter(
        [_spec("unknown", "UNKNOWN_KEY", cost="mystery")],
        model_factory=lambda _: Unknown(),
    )
    assert router.generate_patch(proposal=_proposal(), context=_context()) is None
    assert router.last_attempts[0].status == "skipped"


def test_model_factory_failure_falls_back(monkeypatch):
    monkeypatch.setenv("A_KEY", "a")
    monkeypatch.setenv("B_KEY", "b")

    class Good:
        def generate_patch(self, **kwargs):
            return PatchCandidate(
                "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
                {"app.py": "new\n"},
                "good",
                (),
            )

    def factory(config):
        if config.api_key_env == "A_KEY":
            raise RuntimeError("factory down")
        return Good()

    router = CodingProviderRouter(
        [_spec("a", "A_KEY", 10), _spec("b", "B_KEY", 20)],
        model_factory=factory,
    )
    result = router.generate_patch(proposal=_proposal(), context=_context())
    assert result is not None
    assert result.summary == "good"
    assert router.last_attempts[0].detail == "model_factory failed: RuntimeError"
