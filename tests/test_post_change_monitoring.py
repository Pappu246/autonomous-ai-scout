from __future__ import annotations

from pathlib import Path

from autonomous_agent.cross_project_memory import CrossProjectMemory
from autonomous_agent.post_change_monitoring import (
    ChangeObservation,
    HealthSnapshot,
    ObservationStatus,
    compare_health,
    detect_regression,
    monitor_change,
    record_observation,
)


class Source:
    def __init__(self, observation):
        self.observation = observation

    def observe(self, repository, change_fingerprint):
        assert repository == self.observation.repository
        assert change_fingerprint == self.observation.change_fingerprint
        return self.observation


def observation(*, before=80, after=80, ci="success", verification="verified", before_signals=None, after_signals=None):
    return ChangeObservation(
        repository="owner/repo",
        change_fingerprint="change-1",
        before=HealthSnapshot(before, before_signals or {"tests": "pass"}),
        after=HealthSnapshot(after, after_signals or {"tests": "pass"}, verification),
        ci_conclusion=ci,
        observed_head_sha="a" * 40,
    )


def test_healthy_change_is_not_reported_as_regression():
    finding = detect_regression(observation())
    assert finding.status is ObservationStatus.HEALTHY
    assert finding.repository == "owner/repo"


def test_health_drop_is_regression():
    finding = detect_regression(observation(before=90, after=70))
    assert finding.status is ObservationStatus.REGRESSED
    assert any("health score decreased" in reason for reason in finding.reasons)


def test_failed_ci_is_regression_even_when_health_score_is_unchanged():
    finding = detect_regression(observation(ci="failure"))
    assert finding.status is ObservationStatus.REGRESSED
    assert any("CI conclusion" in reason for reason in finding.reasons)


def test_verification_failure_is_regression():
    finding = detect_regression(observation(verification="failed"))
    assert finding.status is ObservationStatus.REGRESSED


def test_new_failing_signal_is_detected():
    finding = detect_regression(observation(after_signals={"tests": "failed", "docs": "pass"}))
    assert finding.status is ObservationStatus.REGRESSED
    assert "new failing signal: tests" in finding.reasons


def test_improvement_is_distinguished_from_regression():
    status, reasons = compare_health(HealthSnapshot(70, {"tests": "pass"}), HealthSnapshot(90, {"tests": "pass"}), ci_conclusion="success")
    assert status is ObservationStatus.IMPROVED
    assert reasons


def test_insufficient_evidence_is_unknown():
    status, _ = compare_health(HealthSnapshot(80, {}), HealthSnapshot(80, {}), ci_conclusion="pending")
    assert status is ObservationStatus.UNKNOWN


def test_memory_recording_is_evidence_only_and_project_scoped(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    finding = detect_regression(observation(before=90, after=70))
    assert record_observation(memory, finding) is True
    assert len(memory.learn("owner/repo", kind="post_change_observation")) == 1
    assert memory.learn("other/repo", kind="post_change_observation") == []


def test_monitor_uses_existing_observation_boundary(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    item = observation(ci="failure")
    finding = monitor_change(Source(item), item.repository, item.change_fingerprint, memory=memory)
    assert finding.status is ObservationStatus.REGRESSED
    assert len(memory.learn("owner/repo", kind="post_change_observation")) == 1


def test_observation_fingerprint_is_deterministic():
    assert observation().fingerprint == observation().fingerprint
