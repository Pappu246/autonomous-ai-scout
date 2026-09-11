from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .cross_project_memory import CrossProjectMemory, MemoryEvent
from .post_change_monitoring import ChangeObservation, HealthSnapshot, ObservationStatus, RegressionFinding, detect_regression

FAILURES = frozenset({"failure", "failed", "cancelled", "timed_out", "startup_failure", "action_required"})
SUCCESS = frozenset({"success", "successful", "passed"})


class ObservationPipelineError(ValueError):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _time(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class CiRunEvidence:
    run_id: int
    workflow: str
    head_sha: str
    status: str
    conclusion: str | None
    updated_at: str
    jobs: tuple[Mapping[str, Any], ...]

    @property
    def job_failures(self) -> tuple[str, ...]:
        return tuple(sorted(str(j.get("name") or j.get("id")) for j in self.jobs if str(j.get("conclusion") or "").lower() in FAILURES))


@dataclass(frozen=True)
class GitHubPrObservation:
    repository: str
    pr_number: int
    base_sha: str
    head_sha: str
    change_fingerprint: str
    ci_runs: tuple[CiRunEvidence, ...]
    ci_conclusion: str
    verification_status: str
    observed_at: str
    observation: ChangeObservation

    @property
    def fingerprint(self) -> str:
        return _digest({"repository": self.repository, "pr": self.pr_number, "base": self.base_sha, "head": self.head_sha, "change": self.change_fingerprint, "runs": [(r.run_id, r.status, r.conclusion, r.updated_at, r.job_failures) for r in self.ci_runs], "verification": self.verification_status})


def _change_fingerprint(compare: Mapping[str, Any], repository: str, pr_number: int, base_sha: str, head_sha: str) -> str:
    files = []
    for item in compare.get("files") or ():
        if isinstance(item, Mapping):
            files.append({"filename": item.get("filename"), "status": item.get("status"), "additions": item.get("additions", 0), "deletions": item.get("deletions", 0), "patch": item.get("patch", "")})
    files.sort(key=lambda x: str(x["filename"]))
    return _digest({"repository": repository, "pr": pr_number, "base": base_sha, "head": head_sha, "files": files})


def _ci(runs: tuple[CiRunEvidence, ...]) -> tuple[str, str]:
    if not runs:
        return "pending", "no exact-HEAD pull-request workflow run"
    if any(run.job_failures for run in runs):
        return "failure", "one or more exact-HEAD jobs failed"
    conclusions = [str(run.conclusion or run.status).lower() for run in runs]
    if any(item in FAILURES for item in conclusions):
        return "failure", "one or more exact-HEAD workflow runs failed"
    if all(item in SUCCESS for item in conclusions):
        return "success", "all observed exact-HEAD workflow runs succeeded"
    return "pending", "CI is incomplete or inconclusive"


class GitHubPrObservationSource:
    """Read-only adapter from a real GitHub PR to Phase-M evidence."""

    def __init__(self, get_json: Callable[[str, Mapping[str, Any] | None], Any] | None = None, *, health: Callable[[str, str], HealthSnapshot] | None = None, memory: CrossProjectMemory | None = None):
        if get_json is None:
            from .github_audit import gh_get
            get_json = gh_get
        self._get = get_json
        self._health = health
        self._memory = memory

    def observe(self, repository: str, pr_number: int, *, expected_head_sha: str | None = None) -> GitHubPrObservation:
        repository = str(repository).strip()
        if repository.count("/") != 1 or any(not p for p in repository.split("/")):
            raise ObservationPipelineError("invalid repository identity")
        pr = self._get(f"/repos/{repository}/pulls/{int(pr_number)}", None)
        if not isinstance(pr, Mapping):
            raise ObservationPipelineError("pull request metadata unavailable")
        base = pr.get("base") if isinstance(pr.get("base"), Mapping) else {}
        head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
        def repo_name(side: Mapping[str, Any]) -> str:
            repo = side.get("repo")
            return str(repo.get("full_name") if isinstance(repo, Mapping) else repo or "")
        if repo_name(base) != repository or repo_name(head) != repository:
            raise ObservationPipelineError("pull request repository identity mismatch")
        base_sha, head_sha = str(base.get("sha") or ""), str(head.get("sha") or "")
        if len(base_sha) != 40 or len(head_sha) != 40:
            raise ObservationPipelineError("pull request SHA evidence is incomplete")
        if expected_head_sha and head_sha.lower() != expected_head_sha.lower():
            raise ObservationPipelineError("pull request HEAD SHA mismatch")
        compare = self._get(f"/repos/{repository}/compare/{base_sha}...{head_sha}", None)
        if not isinstance(compare, Mapping) or str((compare.get("base_commit") or {}).get("sha", "")) != base_sha:
            raise ObservationPipelineError("base SHA does not match compare evidence")
        commits = compare.get("commits") or []
        if commits and str((commits[-1] or {}).get("sha", "")) != head_sha:
            raise ObservationPipelineError("head SHA does not match compare evidence")
        payload = self._get(f"/repos/{repository}/actions/runs", {"head_sha": head_sha, "event": "pull_request", "per_page": 50})
        runs: list[CiRunEvidence] = []
        for raw in (payload.get("workflow_runs", []) if isinstance(payload, Mapping) else []):
            if not isinstance(raw, Mapping) or str(raw.get("head_sha", "")) != head_sha:
                continue
            run_id = int(raw.get("id", 0) or 0)
            if not run_id:
                continue
            jobs_payload = self._get(f"/repos/{repository}/actions/runs/{run_id}/jobs", {"per_page": 100})
            jobs = jobs_payload.get("jobs", []) if isinstance(jobs_payload, Mapping) else []
            safe_jobs = tuple({"id": j.get("id"), "name": j.get("name"), "status": j.get("status"), "conclusion": j.get("conclusion")} for j in jobs if isinstance(j, Mapping))
            runs.append(CiRunEvidence(run_id, str(raw.get("name") or raw.get("workflow_id") or "workflow"), head_sha, str(raw.get("status") or ""), raw.get("conclusion"), str(raw.get("updated_at") or raw.get("run_started_at") or ""), safe_jobs))
        runs.sort(key=lambda r: (_time(r.updated_at), r.run_id), reverse=True)
        ci, _ = _ci(tuple(runs))
        change = _change_fingerprint(compare, repository, int(pr_number), base_sha, head_sha)
        before = self._before(repository)
        after = self._health(repository, head_sha) if self._health else HealthSnapshot(before.score, dict(before.signals), "verified" if ci == "success" else "failed" if ci == "failure" else "unknown")
        after = HealthSnapshot(after.score, {**dict(after.signals), "ci": ci, "head_sha": head_sha}, after.verification_status)
        observed_at = max((r.updated_at for r in runs), default=str(pr.get("updated_at") or ""))
        return GitHubPrObservation(repository, int(pr_number), base_sha, head_sha, change, tuple(runs), ci, "verified" if ci == "success" and after.verification_status == "verified" else after.verification_status, observed_at, ChangeObservation(repository, change, before, after, ci, head_sha))

    def _before(self, repository: str) -> HealthSnapshot:
        if self._memory:
            for item in reversed(self._memory.learn(repository, kind="post_change_observation")):
                data = item.get("data", {}) if isinstance(item, Mapping) else {}
                if isinstance(data, Mapping) and data.get("after_score") is not None:
                    return HealthSnapshot(float(data["after_score"]), dict(data.get("after_signals", {})), str(data.get("verification_status", "unknown")))
        return HealthSnapshot(0, {}, "unknown")


def _latest_event(memory: CrossProjectMemory, repository: str) -> Mapping[str, Any] | None:
    entries = memory.learn(repository, kind="post_change_observation")
    return entries[-1] if entries else None


def meaningful_github_change(github: GitHubPrObservation, memory: CrossProjectMemory) -> tuple[bool, str, RegressionFinding]:
    finding = detect_regression(github.observation)
    if memory.has(project=github.repository, kind="post_change_observation", fingerprint=finding.observation_fingerprint):
        return False, "duplicate observation", finding
    previous = _latest_event(memory, github.repository)
    if previous:
        data = previous.get("data", {}) if isinstance(previous, Mapping) else {}
        previous_at = str(data.get("observed_at", "")) if isinstance(data, Mapping) else ""
        if _time(github.observed_at) < _time(previous_at):
            return False, "stale/out-of-order observation", finding
        if data.get("head_sha") == github.head_sha and data.get("ci_conclusion") == github.ci_conclusion and data.get("change_fingerprint") == github.change_fingerprint:
            return False, "unchanged exact-HEAD evidence", finding
    if finding.status is ObservationStatus.REGRESSED:
        return True, "new regression/failure", finding
    if finding.status is ObservationStatus.IMPROVED:
        return True, "successful improvement", finding
    if finding.status is ObservationStatus.UNCHANGED:
        return False, "health unchanged", finding
    return False, "no meaningful verified change", finding


def persist_github_observation(memory: CrossProjectMemory, github: GitHubPrObservation) -> tuple[bool, str, RegressionFinding]:
    meaningful, reason, finding = meaningful_github_change(github, memory)
    if not meaningful:
        return False, reason, finding
    ok = memory.record(MemoryEvent(github.repository, "post_change_observation", finding.observation_fingerprint, finding.status.value, {
        "pr_number": github.pr_number,
        "head_sha": github.head_sha,
        "base_sha": github.base_sha,
        "change_fingerprint": github.change_fingerprint,
        "ci_conclusion": github.ci_conclusion,
        "verification_status": github.verification_status,
        "observed_at": github.observed_at,
        "ci_runs": [{"id": r.run_id, "workflow": r.workflow, "status": r.status, "conclusion": r.conclusion, "updated_at": r.updated_at, "jobs": list(r.jobs)} for r in github.ci_runs],
        "after_score": github.observation.after.score,
        "after_signals": dict(github.observation.after.signals),
        "reasons": finding.reasons,
    }))
    return ok, "persisted" if ok else "memory persistence failed", finding
