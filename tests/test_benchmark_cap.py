from autonomous_agent.main import _benchmark_candidates
from autonomous_agent.models import AccessStatus, ModelCandidate


def _candidate(provider: str, model: str) -> ModelCandidate:
    return ModelCandidate(
        provider=provider,
        model=model,
        source_url="https://example.com",
        access_status=AccessStatus.VERIFIED_FREE,
    )


def test_benchmark_candidates_respect_configured_cap(monkeypatch):
    monkeypatch.setenv("MAX_FREE_BENCHMARKS", "2")
    candidates = [_candidate("p", f"m{i}") for i in range(4)]
    assert [c.model for c in _benchmark_candidates(candidates)] == ["m0", "m1"]


def test_benchmark_candidates_invalid_cap_uses_safe_default(monkeypatch):
    monkeypatch.setenv("MAX_FREE_BENCHMARKS", "not-a-number")
    candidates = [_candidate("p", f"m{i}") for i in range(4)]
    assert len(_benchmark_candidates(candidates)) == 2


def test_benchmark_candidates_zero_cap_disables_calls(monkeypatch):
    monkeypatch.setenv("MAX_FREE_BENCHMARKS", "0")
    candidates = [_candidate("p", "m0")]
    assert _benchmark_candidates(candidates) == []
