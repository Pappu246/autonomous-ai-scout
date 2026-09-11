from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .cross_project_memory import CrossProjectMemory, MemoryEvent
from .post_change_monitoring import ChangeObservation, HealthSnapshot, ObservationStatus, RegressionFinding, detect_regression


_TERMINAL_FAILURES = {"failure", "failed", "cancelled", "timed_out", "startup_failure", "action_required"}
_TERMINAL_SUCCESS = {"success", "successful", "passed"}


class GitHubObservationError(ValueError):
    pass


@dataclass(frozen=True)
class CiEvidence:
    run_id: int
    workflow: str
    head_sha: str
    status: str
    conclusion: str | None
    updated_at: str
    jobs: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class GitHubObservation:
    repository: str
    pr_number: int
    head_sha: str
    base_sha: str
    workflow_runs: tuple[CiEvidence, ...]
    change_fingerprint: str
    observation: ChangeObservation

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "repository": self.repository,
                    "pr": self.pr_number,
                    "head": self.head_sha,
                    "base": self.base_sha,
                    "change": self.change_fingerprint,
                    "ci": [(r.run_id, r.conclusion, r.updated_at) for r in self.workflow_runs],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class MeaningfulChange:
    meaningful: bool
    kind: str
    finding: RegressionFinding
    reason: str


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _safe_repo(value: object) -> str:
    text = str(value).strip()
    if text.count("/") != 1 or any(not part for part in text.split("/")):
        raise GitHubObservationError("invalid repository identity")
    return text


def _change_fingerprint(compare: Mapping[str, Any], *, repository: str, pr_number: int, base_sha: str, head_sha: str) -> str:
    files = []
    for item in compare.get("files") or ():
        if not isinstance(item, Mapping):
            continue
        files.append({"filename": str(item.get("filename", "")), "status": str(item.get("status", "")), "additions": int(item.get("additions", 0) or 0), "deletions": int(item.get("deletions", 0) or 0), "patch": str(item.get("patch", ""))})
    files.sort(key=lambda item: item["filename"])
    return _digest({"repository": repository, "pr": pr_number, "base": base_sha, "head": head_sha, "files": files})


def _job_evidence(get_json: Callable[[str, Mapping[str, Any] | None], Any], repository: str, run_id: int) -> tuple[Mapping[str, Any], ...]:
    payload = get_json(f"/repos/{repository}/actions/runs/{run_id}/jobs", {"per_page": 100})
    jobs = payload.get("jobs", []) if isinstance(payload, Mapping) else []
    result = []
    for job in jobs:
        if isinstance(job, Mapping):
            result.append({"id": job.get("id"), "name": job.get("name"), "status": job.get("status"), "conclusion": job.get("conclusion")})
    return tuple(result)


def _ci_conclusion(runs: tuple[CiEvidence, ...]) -> tuple[str, str]:
    if not runs:
        return "pending", "no pull-request workflow run was found for the exact PR HEAD"
    conclusions = [str(run.conclusion or run.status or "").lower() for run in runs]
    if any(item in _TERMINAL_FAILURES for item in conclusions):
        return "failure", "at least one exact-HEAD workflow run failed or was cancelled"
    if all(item in _TERMINAL_SUCCESS for item in conclusions):
        return "success", "all observed exact-HEAD workflow runs succeeded"
    return "pending", "at least one exact-HEAD workflow run is not terminal-success"


class GitHubObservationSource:
    """Read-only GitHub PR/CI adapter. It never creates, updates, merges, or authorizes anything."""

    def __init__(self, *, get_json: Callable[[str, Mapping[str, Any] | None], Any] | None = None, health_provider: Callable[[str, str], HealthSnapshot] | None = None, memory: CrossProjectMemory | None = None):
        if get_json is None:
            from .github_audit import gh_get
            get_json = gh_get
        self._get = get_json
        self._health = health_provider
        self._memory = memory

    def observe_pr(self, repository: str, pr_number: int, *, expected_head_sha: str | None = None) -> GitHubObservation:
        repository = _safe_repo(repository)
        pr = self._get(f"/repos/{repository}/pulls/{int(pr_number)}", None)
        if not isinstance(pr, Mapping):
            raise GitHubObservationError("pull request metadata unavailable")
        base = pr.get("base") if isinstance(pr.get("base"), Mapping) else {}
        head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
        base_repo = ((base.get("repo") or {}).get("full_name") if isinstance(base.get("repo"), Mapping) else base.get("repo"))
        head_repo = ((head.get("repo") or {}).get("full_name") if isinstance(head.get("repo"), Mapping) else head.get("repo"))
        if str(base_repo or "") != repository or str(head_repo or "") != repository:
            raise GitHubObservationError("pull request repository identity mismatch")
        base_sha, head_sha = str(base.get("sha") or ""), str(head.get("sha") or "")
        if len(base_sha) != 40 or len(head_sha) != 40:
            raise GitHubObservationError("pull request SHA evidence is incomplete")
        if expected_head_sha is not None and head_sha.lower() != expected_head_sha.lower():
            raise GitHubObservationError("pull request HEAD SHA mismatch")
        compare = self._get(f"/repos/{repository}/compare/{base_sha}...{head_sha}", None)
        if not isinstance(compare, Mapping) or str(compare.get("base_commit", {}).get("sha", "")) != base_sha or str(compare.get("merge_base_commit", {}).get("sha", "")) not in {base_sha, ""}:
            raise GitHubObservationError("base SHA could not be correlated with the compare evidence")
        compare_head = str(compare.get("commits", [{}])[-1].get("sha", "")) if compare.get("commits") else ""
        if compare_head and compare_head != head_sha:
            raise GitHubObservationError("compare evidence HEAD SHA mismatch")
        runs_payload = self._get(f"/repos/{repository}/actions/runs", {"head_sha": head_sha, "event": "pull_request", "per_page": 50})
        raw_runs = runs_payload.get("workflow_runs", []) if isinstance(runs_payload, Mapping) else []
        runs: list[CiEvidence] = []
        for item in raw_runs:
            if not isinstance(item, Mapping) or str(item.get("head_sha", "")) != head_sha:
                continue
            run_id = int(item.get("id", 0) or 0)
            if not run_id:
                continue
            jobs = _job_evidence(self._get, repository, run_id)
            runs.append(CiEvidence(run_id, str(item.get("name") or item.get("workflow_id") or "workflow"), head_sha, str(item.get("status") or ""), item.get("conclusion"), str(item.get("updated_at") or item.get("run_started_at") or ""), jobs))
        runs.sort(key=lambda item: (_parse_time(item.updated_at), item.run_id), reverse=True)
        ci, _ = _ci_conclusion(tuple(runs))
        change = _change_fingerprint(compare, repository=repository, pr_number=int(pr_number), base_sha=base_sha, head_sha=head_sha)
        before = self._before_health(repository, change)
        after_score = before.score
        after_signals = dict(before.signals)
        after_signals["ci"] = ci
        after_signals["head_sha"] = head_sha
        after = HealthSnapshot(after_score, after_signals, "verified" if ci == "success" else "unknown")
        if self._health is not None:
            before = self._health(repository, base_sha)
            after = self._health(repository, head_sha)
            after = HealthSnapshot(after.score, {**dict(after.signals), "ci": ci}, after.verification_status if ci == "success" else "failed" if ci == "failure" else after.verification_status)
        observation = ChangeObservation(repository, change, before, after, ci, head_sha)
        return GitHubObservation(repository, int(pr_number), head_sha, base_sha, tuple(runs), change, observation)

    def _before_health(self, repository: str, change_fingerprint: str) -> HealthSnapshot:
        if self._memory is not None:
            entries = self._memory.learn(repository, kind="post_change_observation")
            for entry in reversed(entries):
                data = entry.get("data", {}) if isinstance(entry, Mapping) else {}
                if isinstance(data, Mapping) and data.get("after_score") is not None:
                    return HealthSnapshot(float(data.get("after_score", 0)), dict(data.get("after_signals", {})), str(data.get("verification_status", "unknown")))
        return HealthSnapshot(0, {}, "unknown")


def meaningful_change(finding: RegressionFinding, *, memory: CrossProjectMemory | None = None) -> MeaningfulChange:
    if memory is not None:
        try:
            if memory.has(project=finding.repository, kind="post_change_observation", fingerprint=finding.observation_fingerprint):
                return MeaningfulChange(False, "duplicate", finding, "identical observation already persisted")
        except Exception:
            pass
    if finding.status is ObservationStatus.REGRESSED:
        return MeaningfulChange(True, "regression", finding, "new failure, verification failure, or health regression")
    if finding.status is ObservationStatus.IMPROVED:
        return MeaningfulChange(True, "improvement", finding, "health improved after the observed change")
    if finding.status is ObservationStatus.UNCHANGED:
        return MeaningfulChange(False, "unchanged", finding, "observable signals changed without a health regression")
    if finding.status is ObservationStatus.HEALTHY:
        return MeaningfulChange(False, "healthy", finding, "successful verified state without a new meaningful change")
    return MeaningfulChange(False, "unknown", finding, "insufficient evidence for a meaningful notification")


def record_github_observation(memory: CrossProjectMemory, github: GitHubObservation) -> MeaningfulChange:
    finding = detect_regression(github.observation)
    change = meaningful_change(finding, memory=memory)
    if not change.meaningful and change.kind == "duplicate":
        return change
    # Store only safe, bounded evidence through the existing memory authority.
    ok = memory.record(
        MemoryEvent(
            project=github.repository,
            kind="post_change_observation",
            fingerprint=finding.observation_fingerprint,
            outcome=finding.status.value,
            data={
                "pr_number": github.pr_number,
                "head_sha": github.head_sha,
                "base_sha": github.base_sha,
                "change_fingerprint": github.change_fingerprint,
                "ci_runs": [{"id": run.run_id, "workflow": run.workflow, "status": run.status, "conclusion": run.conclusion, "updated_at": run.updated_at, "jobs": list(run.jobs)} for run in github.workflow_runs],
                "after_score": github.observation.after.score,
                "after_signals": dict(github.observation.after.signals),
                "verification_status": github.observation.after.verification_status,
                "reasons": finding.reasons,
            },
        )
    )
    if not ok:
        return MeaningfulChange(False, "persistence_failed", finding, "observation could not be persisted; no notification or authority change")
    return change


def monitor_github_pr(source: GitHubObservationSource, repository: str, pr_number: int, *, expected_head_sha: str | None = None, memory: CrossProjectMemory | None = None) -> MeaningfulChange:
    github = source.observe_pr(repository, pr_number, expected_head_sha=expected_head_sha)
    finding = detect_regression(github.observation)
    change = meaningful_change(finding, memory=memory)
    if memory is not None and change.kind != "duplicate":
        return record_github_observation(memory, github)
    return change
