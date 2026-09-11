from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .cross_project_memory import CrossProjectMemory, MemoryEvent
from .github_audit import gh_get
from .post_change_monitoring import ChangeObservation, HealthSnapshot, ObservationStatus, RegressionFinding, detect_regression, record_observation


class GitHubObservationError(RuntimeError):
    """Raised when GitHub evidence is missing, inconsistent, or stale."""


@dataclass(frozen=True)
class CIRunEvidence:
    run_id: int
    name: str
    status: str
    conclusion: str | None
    head_sha: str
    updated_at: str


@dataclass(frozen=True)
class CheckEvidence:
    name: str
    status: str
    conclusion: str | None
    head_sha: str


@dataclass(frozen=True)
class GitHubObservation:
    repository: str
    pull_request: int
    head_sha: str
    base_sha: str
    change_fingerprint: str
    ci_conclusion: str
    runs: tuple[CIRunEvidence, ...]
    checks: tuple[CheckEvidence, ...]
    verification_status: str
    observed_at: str

    @property
    def fingerprint(self) -> str:
        payload = {
            "repository": self.repository,
            "pull_request": self.pull_request,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "change_fingerprint": self.change_fingerprint,
            "ci_conclusion": self.ci_conclusion,
            "runs": [{"run_id": r.run_id, "name": r.name, "status": r.status, "conclusion": r.conclusion, "head_sha": r.head_sha} for r in self.runs],
            "checks": [{"name": c.name, "status": c.status, "conclusion": c.conclusion, "head_sha": c.head_sha} for c in self.checks],
            "verification_status": self.verification_status,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class HealthSnapshotSource(Protocol):
    def snapshot(self, repository: str, ref: str) -> HealthSnapshot: ...


def _safe_string(value: object) -> str:
    return str(value)[:512]


def _change_fingerprint(repository: str, base_sha: str, head_sha: str, compare: dict[str, Any]) -> str:
    files = compare.get("files", []) if isinstance(compare, dict) else []
    summary = []
    if isinstance(files, list):
        for item in files[:100]:
            if isinstance(item, dict):
                summary.append({"filename": _safe_string(item.get("filename", "")), "status": _safe_string(item.get("status", "")), "additions": item.get("additions", 0), "deletions": item.get("deletions", 0)})
    payload = {"repository": repository, "base_sha": base_sha, "head_sha": head_sha, "files": summary, "ahead_by": compare.get("ahead_by", 0), "behind_by": compare.get("behind_by", 0)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _ci_conclusion(runs: tuple[CIRunEvidence, ...], checks: tuple[CheckEvidence, ...]) -> str:
    if not runs:
        return "unknown"
    conclusions = [run.conclusion.strip().lower() for run in runs if run.status.strip().lower() == "completed" and run.conclusion]
    conclusions.extend(check.conclusion.strip().lower() for check in checks if check.status.strip().lower() == "completed" and check.conclusion)
    failures = {"failure", "failed", "cancelled", "timed_out", "startup_failure", "action_required", "error"}
    if any(item in failures for item in conclusions):
        return "failure"
    pending = any(run.status.strip().lower() not in {"completed", ""} for run in runs) or any(check.status.strip().lower() not in {"completed", ""} for check in checks)
    if pending:
        return "pending"
    if conclusions and all(item in {"success", "successful", "passed"} for item in conclusions):
        return "success"
    return "unknown"


def _health_from_evidence(ci_conclusion: str, verification_status: str) -> HealthSnapshot:
    return HealthSnapshot(
        score=100.0 if ci_conclusion == "success" and verification_status == "verified" else 0.0,
        signals={"github_ci": ci_conclusion, "commit_verification": verification_status},
        verification_status=verification_status,
    )


def observe_github_pull_request(
    repository: str,
    pull_request: int,
    *,
    memory: CrossProjectMemory | None = None,
    health_source: HealthSnapshotSource | None = None,
    fetch: Callable[[str, dict[str, Any] | None], Any] = gh_get,
) -> tuple[GitHubObservation, RegressionFinding | None]:
    if not repository or "/" not in repository or repository.count("/") != 1:
        raise GitHubObservationError("invalid repository identity")
    pr = fetch(f"/repos/{repository}/pulls/{int(pull_request)}")
    if not isinstance(pr, dict):
        raise GitHubObservationError("pull request could not be read")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    base_repo = (base.get("repo") or {}).get("full_name")
    head_sha = str(head.get("sha") or "")
    base_sha = str(base.get("sha") or "")
    if head_repo != repository or base_repo != repository or len(head_sha) != 40 or len(base_sha) != 40:
        raise GitHubObservationError("pull request repository or SHA identity mismatch")

    compare = fetch(f"/repos/{repository}/compare/{base_sha}...{head_sha}")
    if not isinstance(compare, dict):
        raise GitHubObservationError("change comparison could not be read")
    if str((compare.get("base_commit") or {}).get("sha", base_sha)) != base_sha:
        raise GitHubObservationError("compare base SHA mismatch")
    change_fingerprint = _change_fingerprint(repository, base_sha, head_sha, compare)

    raw_runs = fetch(f"/repos/{repository}/actions/runs", {"head_sha": head_sha, "event": "pull_request", "per_page": 20})
    runs: list[CIRunEvidence] = []
    if isinstance(raw_runs, dict):
        for item in raw_runs.get("workflow_runs", [])[:20]:
            if not isinstance(item, dict) or str(item.get("head_sha") or "") != head_sha:
                continue
            runs.append(CIRunEvidence(int(item.get("id", 0)), _safe_string(item.get("name", "")), _safe_string(item.get("status", "")), _safe_string(item.get("conclusion")) if item.get("conclusion") is not None else None, head_sha, _safe_string(item.get("updated_at", ""))))
    runs_tuple = tuple(sorted(runs, key=lambda item: (item.updated_at, item.run_id)))

    raw_checks = fetch(f"/repos/{repository}/commits/{head_sha}/check-runs", {"per_page": 100})
    checks: list[CheckEvidence] = []
    if isinstance(raw_checks, dict):
        for item in raw_checks.get("check_runs", [])[:100]:
            if not isinstance(item, dict):
                continue
            check_head = str(item.get("head_sha") or head_sha)
            if check_head != head_sha:
                continue
            checks.append(CheckEvidence(_safe_string(item.get("name", "")), _safe_string(item.get("status", "")), _safe_string(item.get("conclusion")) if item.get("conclusion") is not None else None, head_sha))
    checks_tuple = tuple(sorted(checks, key=lambda item: item.name))

    commit = fetch(f"/repos/{repository}/commits/{head_sha}")
    verification = (commit.get("commit") or {}).get("verification") if isinstance(commit, dict) else None
    verification_status = "verified" if isinstance(verification, dict) and verification.get("verified") else "unverified"
    ci = _ci_conclusion(runs_tuple, checks_tuple)
    if ci == "failure":
        verification_status = "failed"
    elif ci in {"unknown", "pending"}:
        verification_status = "unknown"
    observed_at = max([item.updated_at for item in runs_tuple if item.updated_at] or [str(pr.get("updated_at") or "")])
    evidence = GitHubObservation(repository, int(pull_request), head_sha, base_sha, change_fingerprint, ci, runs_tuple, checks_tuple, verification_status, observed_at)

    before = health_source.snapshot(repository, base_sha) if health_source else _health_from_evidence("unknown", "unknown")
    after = health_source.snapshot(repository, head_sha) if health_source else _health_from_evidence(ci, verification_status)
    finding = detect_regression(ChangeObservation(repository, change_fingerprint, before, after, ci, head_sha))

    if memory is not None:
        if memory.has(project=repository, kind="github_observation", fingerprint=evidence.fingerprint):
            return evidence, None
        prior = [item for item in memory.learn(repository, kind="github_observation") if item.get("data", {}).get("change_fingerprint") == change_fingerprint]
        if prior:
            previous_observed = str(prior[-1].get("data", {}).get("observed_at", ""))
            if previous_observed and observed_at and observed_at < previous_observed:
                return evidence, None
            previous_outcome = str(prior[-1].get("outcome", "")).strip().lower()
            if previous_outcome == ObservationStatus.REGRESSED.value and ci == "success" and verification_status == "verified":
                finding = RegressionFinding(
                    repository=finding.repository,
                    change_fingerprint=finding.change_fingerprint,
                    observation_fingerprint=finding.observation_fingerprint,
                    status=ObservationStatus.IMPROVED,
                    reasons=("CI recovered after a previously regressed observation",),
                    evidence=finding.evidence,
                )
        if not record_observation(memory, finding):
            return evidence, finding
        memory.record(MemoryEvent(project=repository, kind="github_observation", fingerprint=evidence.fingerprint, outcome=finding.status.value, data={"pull_request": pull_request, "head_sha": head_sha, "base_sha": base_sha, "change_fingerprint": change_fingerprint, "ci_conclusion": ci, "observed_at": observed_at}))
    return evidence, finding
