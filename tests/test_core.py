from autonomous_agent.models import AccessStatus, ModelCandidate
from autonomous_agent.ranking import choose_model
from autonomous_agent.state import StateStore


def candidate(status: AccessStatus) -> ModelCandidate:
    return ModelCandidate(
        provider="test",
        model="fast-model",
        source_url="https://example.com/pricing",
        access_status=status,
    )


def test_router_rejects_non_free_candidates():
    assert choose_model([candidate(AccessStatus.PAID_ONLY)], "general") is None


def test_router_selects_verified_free_candidate():
    selected = choose_model([candidate(AccessStatus.VERIFIED_FREE)], "general")
    assert selected is not None
    assert selected.access_status is AccessStatus.VERIFIED_FREE


def test_state_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    payload = {"run": 1, "models": ["x"]}
    store.save(payload)
    assert store.load() == payload
