from __future__ import annotations

from autonomous_agent.models import AccessStatus, ModelCandidate
from autonomous_agent.opportunities import score_opportunity
from autonomous_agent.sources import SourceCheck, source_has_free_signal
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
