from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from .cross_project_memory import CrossProjectMemory, MemoryEvent


class ObservationStatus(str, Enum):
    HEALTHY = "healthy"
    REGRESSED = "regressed"
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HealthSnapshot:
    score: float
    signals: Mapping[str, object]
    verification_status: str = "verified"


@dataclass(frozen=True)
class ChangeObservation:
    repository: str
    change_fingerprint: str
    before: HealthSnapshot
    after: HealthSnapshot
    ci_conclusion: str
    observed_head_sha: str | None = None

    @property
    def fingerprint(self) -> str:
        payload = {
            "repository": self.repository,
            "change_fingerprint": self.change_fingerprint,
            "before": {"score": self.before.score, "signals": dict(sorted(self.before.signals.items()))},
            "after": {"score": self.after.score, "signals": dict(sorted(self.after.signals.items()))},
            "ci_conclusion": self.ci_conclusion,
            "observed_head_sha": self.observed_head_sha,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class RegressionFinding:
    repository: str
    change_fingerprint: str
    observation_fingerprint: str
    status: ObservationStatus
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]


class ChangeObservationSource(Protocol):
    def observe(self, repository: str, change_fingerprint: str) -> ChangeObservation: ...


def compare_health(before: HealthSnapshot, after: HealthSnapshot, *, ci_conclusion: str) -> tuple[ObservationStatus, tuple[str, ...]]:
    reasons: list[str] = []
    ci = ci_conclusion.strip().lower()
    if ci in {"failure", "failed", "cancelled", "timed_out", "startup_failure", "action_required"}:
        reasons.append(f"CI conclusion: {ci_conclusion}")
    if after.verification_status.strip().lower() != "verified":
        reasons.append(f"verification status: {after.verification_status}")
    if after.score < before.score:
        reasons.append(f"health score decreased from {before.score:g} to {after.score:g}")
    changed_failures = sorted(
        key for key, value in after.signals.items()
        if str(value).strip().lower() in {"fail", "failed", "failure", "broken", "regressed"}
        and str(before.signals.get(key, "")).strip().lower() not in {"fail", "failed", "failure", "broken", "regressed"}
    )
    reasons.extend(f"new failing signal: {key}" for key in changed_failures)
    if reasons:
        return ObservationStatus.REGRESSED, tuple(reasons)
    if after.score > before.score:
        return ObservationStatus.IMPROVED, (f"health score increased from {before.score:g} to {after.score:g}",)
    if dict(before.signals) != dict(after.signals):
        return ObservationStatus.UNCHANGED, ("health score unchanged; observable signals changed without a regression",)
    if ci in {"success", "successful", "passed"} and after.verification_status.strip().lower() == "verified":
        return ObservationStatus.HEALTHY, ("CI succeeded and verification remained verified",)
    return ObservationStatus.UNKNOWN, ("post-change evidence was insufficient for a stronger conclusion",)


def detect_regression(observation: ChangeObservation) -> RegressionFinding:
    status, reasons = compare_health(observation.before, observation.after, ci_conclusion=observation.ci_conclusion)
    evidence = (
        f"change={observation.change_fingerprint}",
        f"ci={observation.ci_conclusion}",
        f"before_score={observation.before.score:g}",
        f"after_score={observation.after.score:g}",
    )
    return RegressionFinding(
        repository=observation.repository,
        change_fingerprint=observation.change_fingerprint,
        observation_fingerprint=observation.fingerprint,
        status=status,
        reasons=reasons,
        evidence=evidence,
    )


def record_observation(memory: CrossProjectMemory, finding: RegressionFinding) -> bool:
    return memory.record(
        MemoryEvent(
            project=finding.repository,
            kind="post_change_observation",
            fingerprint=finding.observation_fingerprint,
            outcome=finding.status.value,
            data={"change_fingerprint": finding.change_fingerprint, "reasons": finding.reasons, "evidence": finding.evidence},
        )
    )


def monitor_change(source: ChangeObservationSource, repository: str, change_fingerprint: str, *, memory: CrossProjectMemory | None = None) -> RegressionFinding:
    observation = source.observe(repository, change_fingerprint)
    finding = detect_regression(observation)
    if memory is not None:
        record_observation(memory, finding)
    return finding
