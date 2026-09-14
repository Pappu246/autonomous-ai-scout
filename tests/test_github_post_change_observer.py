from __future__ import annotations

from pathlib import Path

import pytest

from autonomous_agent.cross_project_memory import CrossProjectMemory
from autonomous_agent.github_post_change_observer import GitHubObservationError, observe_github_pull_request


HEAD = "a" * 40
BASE = "b" * 40
OLD = "c" * 40


def payload(*, repository="owner/repo", head=HEAD, base=BASE, run_head=HEAD, ci="success", updated_at="2026-09-11T10:00:00Z", pr_updated="2026-09-11T09:00:00Z", verification=True, secret=None):
    def fetch(path, params=None):
        if path == f"/repos/{repository}/pulls/1":
            return {"head": {"sha": head, "repo": {"full_name": repository}}, "base": {"sha": base, "repo": {"full_name": repository}}, "updated_at": pr_updated}
        if path == f"/repos/{repository}/compare/{base}...{head}":
            return {"ahead_by": 1, "behind_by": 0, "files": [{"filename": "app.py", "status": "modified", "additions": 2, "deletions": 1}]}
        if path == f"/repos/{repository}/actions/runs":
            return {"workflow_runs": [{"id": 7, "name": "CI", "status": "completed", "conclusion": ci, "head_sha": run_head, "updated_at": updated_at}]}
        if path == f"/repos/{repository}/commits/{head}/check-runs":
            return {"check_runs": [{"name": "test", "status": "completed", "conclusion": ci, "head_sha": head}]}
        if path == f"/repos/{repository}/commits/{head}":
            return {"commit": {"verification": {"verified": verification, "message": secret or ""}}}
        raise AssertionError(path)
    return fetch


def test_wrong_repository_fails_closed():
    with pytest.raises(GitHubObservationError):
        observe_github_pull_request("owner/repo/other", 1, fetch=payload())


def test_wrong_head_sha_is_rejected():
    with pytest.raises(GitHubObservationError):
        observe_github_pull_request("owner/repo", 1, fetch=payload(head="bad"))


def test_stale_ci_for_old_sha_is_not_treated_as_current_success():
    evidence, finding = observe_github_pull_request("owner/repo", 1, fetch=payload(run_head=OLD, ci="success"))
    assert evidence.ci_conclusion == "unknown"
    assert finding is not None


def test_duplicate_event_is_filtered_by_existing_memory(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    first, finding = observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload())
    second, duplicate = observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload())
    assert first.fingerprint == second.fingerprint
    assert finding is not None
    assert duplicate is None
    assert len(memory.learn("owner/repo", kind="github_observation")) == 1


def test_reordered_event_is_ignored(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload(updated_at="2026-09-11T11:00:00Z"))
    _, finding = observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload(updated_at="2026-09-11T10:00:00Z"))
    assert finding is None
    assert len(memory.learn("owner/repo", kind="github_observation")) == 1


def test_failed_to_recovered_transition_is_meaningful(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload(ci="failure", updated_at="2026-09-11T10:00:00Z", verification=False))
    _, finding = observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload(ci="success", updated_at="2026-09-11T11:00:00Z", verification=True))
    assert finding is not None
    assert finding.status.value == "improved"
    assert any("recovered" in reason for reason in finding.reasons)


def test_regression_is_reported_with_health_source():
    class Health:
        def snapshot(self, repository, ref):
            from autonomous_agent.post_change_monitoring import HealthSnapshot
            return HealthSnapshot(90 if ref == BASE else 70, {"tests": "pass" if ref == BASE else "failed"}, "verified")

    _, finding = observe_github_pull_request("owner/repo", 1, health_source=Health(), fetch=payload())
    assert finding is not None
    assert finding.status.value == "regressed"


def test_memory_persistence_failure_does_not_authorize_anything(tmp_path: Path):
    class FailingMemory(CrossProjectMemory):
        def record(self, event):
            return False

    memory = FailingMemory(tmp_path / "memory.json")
    _, finding = observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload())
    assert finding is not None
    assert memory.learn("owner/repo") == ()


def test_cross_project_memory_isolation(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload())
    assert memory.learn("other/repo", kind="github_observation") == ()


def test_secret_safe_evidence_does_not_persist_secret(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    secret = "API key=super-secret-value"
    observe_github_pull_request("owner/repo", 1, memory=memory, fetch=payload(secret=secret))
    text = (tmp_path / "memory.json").read_text(encoding="utf-8")
    assert "super-secret-value" not in text


def test_fingerprints_are_deterministic():
    first, _ = observe_github_pull_request("owner/repo", 1, fetch=payload())
    second, _ = observe_github_pull_request("owner/repo", 1, fetch=payload())
    assert first.fingerprint == second.fingerprint
    assert first.change_fingerprint == second.change_fingerprint
