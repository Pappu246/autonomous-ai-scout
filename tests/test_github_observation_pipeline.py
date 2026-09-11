from __future__ import annotations

from autonomous_agent.cross_project_memory import CrossProjectMemory
from autonomous_agent.github_observation_pipeline import GitHubPrObservationSource, ObservationPipelineError, persist_github_observation
from autonomous_agent.post_change_monitoring import HealthSnapshot

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def fixture_getter(*, repo="owner/repo", head=SHA_A, base=SHA_B, run_conclusion="success", run_head=None, job_conclusion="success", run_updated="2026-09-11T10:00:00Z", base_repo=None, head_repo=None):
    def get(path, params):
        if path.endswith("/pulls/7"):
            return {"number": 7, "updated_at": run_updated, "base": {"sha": base, "repo": {"full_name": base_repo or repo}}, "head": {"sha": head, "repo": {"full_name": head_repo or repo}}}
        if "/compare/" in path:
            return {"base_commit": {"sha": base}, "commits": [{"sha": head}], "files": [{"filename": "app.py", "status": "modified", "additions": 1, "deletions": 1, "patch": "-old\\n+new"}]}
        if path.endswith("/actions/runs"):
            return {"workflow_runs": [{"id": 100, "name": "CI", "head_sha": run_head or head, "status": "completed", "conclusion": run_conclusion, "updated_at": run_updated}]}
        if "/actions/runs/100/jobs" in path:
            return {"jobs": [{"id": 900, "name": "tests", "status": "completed", "conclusion": job_conclusion}]}
        raise AssertionError(path)
    return get


def health(repo, ref):
    return HealthSnapshot(80 if ref == SHA_B else 90, {"tests": "pass"}, "verified")


def test_real_pr_identity_change_ci_jobs_and_fingerprint_are_correlated():
    source = GitHubPrObservationSource(fixture_getter(), health=health)
    item = source.observe("owner/repo", 7, expected_head_sha=SHA_A)
    assert item.repository == "owner/repo" and item.pr_number == 7
    assert item.base_sha == SHA_B and item.head_sha == SHA_A
    assert item.change_fingerprint and item.ci_runs[0].run_id == 100
    assert item.ci_runs[0].jobs[0]["name"] == "tests"
    assert item.ci_conclusion == "success"
    assert item.verification_status == "verified"


def test_wrong_sha_fails_closed():
    source = GitHubPrObservationSource(fixture_getter())
    try:
        source.observe("owner/repo", 7, expected_head_sha=SHA_C)
    except ObservationPipelineError as exc:
        assert "HEAD SHA mismatch" in str(exc)
    else:
        assert False


def test_wrong_repository_fails_closed():
    source = GitHubPrObservationSource(fixture_getter(head_repo="other/repo"))
    try:
        source.observe("owner/repo", 7)
    except ObservationPipelineError as exc:
        assert "repository identity mismatch" in str(exc)
    else:
        assert False


def test_stale_ci_for_old_head_is_not_correlated():
    source = GitHubPrObservationSource(fixture_getter(run_head=SHA_C))
    item = source.observe("owner/repo", 7)
    assert item.ci_conclusion == "pending"
    assert item.verification_status == "unknown"


def test_failed_to_recovered_transition_is_meaningful(tmp_path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    failed = GitHubPrObservationSource(fixture_getter(run_conclusion="failure", job_conclusion="failure", run_updated="2026-09-11T10:00:00Z"), health=health).observe("owner/repo", 7)
    ok, _, finding = persist_github_observation(memory, failed)
    assert ok and finding.status.value == "regressed"
    recovered = GitHubPrObservationSource(fixture_getter(run_conclusion="success", job_conclusion="success", run_updated="2026-09-11T11:00:00Z"), health=health).observe("owner/repo", 7)
    ok, reason, finding = persist_github_observation(memory, recovered)
    assert ok and finding.status.value == "improved"
    assert reason == "persisted"


def test_duplicate_event_is_suppressed(tmp_path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    source = GitHubPrObservationSource(fixture_getter(), health=health)
    item = source.observe("owner/repo", 7)
    assert persist_github_observation(memory, item)[0]
    ok, reason, _ = persist_github_observation(memory, item)
    assert not ok and "duplicate" in reason


def test_reordered_event_is_suppressed(tmp_path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    newer = GitHubPrObservationSource(fixture_getter(run_updated="2026-09-11T12:00:00Z"), health=health).observe("owner/repo", 7)
    older = GitHubPrObservationSource(fixture_getter(run_updated="2026-09-11T11:00:00Z", run_conclusion="failure", job_conclusion="failure"), health=health).observe("owner/repo", 7)
    assert persist_github_observation(memory, newer)[0]
    ok, reason, _ = persist_github_observation(memory, older)
    assert not ok and "stale/out-of-order" in reason


def test_regression_is_persisted_as_safe_evidence(tmp_path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    item = GitHubPrObservationSource(fixture_getter(run_conclusion="failure", job_conclusion="failure"), health=health).observe("owner/repo", 7)
    ok, _, _ = persist_github_observation(memory, item)
    assert ok
    entries = memory.learn("owner/repo", kind="post_change_observation")
    assert len(entries) == 1
    assert entries[0]["data"]["pr_number"] == 7
    assert "authorization" not in str(entries[0]).lower()


def test_memory_persistence_failure_does_not_authorize_or_notify(tmp_path):
    class FailingMemory(CrossProjectMemory):
        def _save(self, entries):
            return False
    memory = FailingMemory(tmp_path / "memory.json")
    item = GitHubPrObservationSource(fixture_getter(), health=health).observe("owner/repo", 7)
    ok, reason, _ = persist_github_observation(memory, item)
    assert not ok and reason == "memory persistence failed"


def test_cross_project_isolation(tmp_path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    item = GitHubPrObservationSource(fixture_getter(repo="owner/repo-a"), health=health).observe("owner/repo-a", 7)
    persist_github_observation(memory, item)
    assert memory.learn("owner/repo-b", kind="post_change_observation") == ()


def test_secret_safe_evidence_and_deterministic_fingerprint():
    source = GitHubPrObservationSource(fixture_getter())
    one = source.observe("owner/repo", 7)
    two = source.observe("owner/repo", 7)
    assert one.fingerprint == two.fingerprint
    assert one.change_fingerprint == two.change_fingerprint
    assert "supersecret" not in str(one)


def test_job_failure_overrides_successful_workflow_run():
    item = GitHubPrObservationSource(fixture_getter(run_conclusion="success", job_conclusion="failure"), health=health).observe("owner/repo", 7)
    assert item.ci_conclusion == "failure"
    assert item.verification_status == "failed"


def test_no_ci_run_is_inconclusive_not_success():
    def get(path, params):
        base = fixture_getter()
        if path.endswith("/actions/runs"):
            return {"workflow_runs": []}
        return base(path, params)
    item = GitHubPrObservationSource(get, health=health).observe("owner/repo", 7)
    assert item.ci_conclusion == "pending"
    assert item.verification_status == "unknown"
