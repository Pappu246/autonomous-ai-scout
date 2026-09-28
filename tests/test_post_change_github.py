from __future__ import annotations

from autonomous_agent.post_change_evidence import CommitObservation, PullRequestObservation
from autonomous_agent.post_change_final import CheckConclusion, Phase6Evidence
from autonomous_agent.post_change_github import GitHubPostChangeEvidenceProvider
from autonomous_agent.post_change_snapshot import SnapshotFileStatus
from autonomous_agent.post_change_verification import FileManifestEntry, RequiredCheck, TestPolicy, VerificationRequest


SHA = "a" * 40
OTHER = "b" * 40
DIGEST = "1" * 64
REPO = "owner/repo"


class FakeTransport:
    def __init__(self):
        self.files = [{
            "filename": "app.py",
            "status": "modified",
            "sha": "blob",
        }]
        self.tree = {
            "truncated": False,
            "tree": [{"path": "app.py", "mode": "100644", "type": "blob", "sha": "gitsha"}],
        }
        self.contents = {"app.py": b"print('new')\n"}
        self.runs = [
            {"id": 10, "name": "CI", "event": "push", "status": "completed", "conclusion": "success", "head_sha": SHA},
            {"id": 11, "name": "CI", "event": "pull_request", "status": "completed", "conclusion": "success", "head_sha": SHA},
        ]

    def head_sha(self, repository, branch):
        return SHA

    def get(self, repository, pull_request):
        return {
            "number": 7,
            "state": "open",
            "draft": False,
            "merged": False,
            "head": {
                "ref": "improvement/one",
                "sha": SHA,
                "repo": {"full_name": REPO},
            },
            "base": {"ref": "main"},
        }

    def pull_request_files(self, repository, pull_request, *, page=1, per_page=100):
        return self.files if page == 1 else []

    def git_tree(self, repository, commit_sha, *, recursive=True):
        return self.tree

    def read_file_bytes_at_ref(self, repository, path, ref):
        return self.contents[path]

    def workflow_runs(self, repository, head_sha, *, page=1, per_page=100):
        return self.runs if page == 1 else []


def request() -> VerificationRequest:
    import hashlib

    digest = hashlib.sha256(b"print('new')\n").hexdigest()
    return VerificationRequest(
        repository=REPO,
        pull_request_number=7,
        expected_head_branch="improvement/one",
        expected_base_branch="main",
        expected_commit_sha=SHA,
        expected_files=(FileManifestEntry("app.py", digest),),
        test_policy=TestPolicy((RequiredCheck("CI"),)),
    )


def test_collects_all_four_evidence_types():
    evidence = GitHubPostChangeEvidenceProvider(FakeTransport()).collect(request())
    assert isinstance(evidence, Phase6Evidence)
    assert isinstance(evidence.commit, CommitObservation)
    assert isinstance(evidence.pull_request, PullRequestObservation)
    assert evidence.snapshot is not None
    assert evidence.tests is not None


def test_snapshot_maps_exact_sha256_and_status():
    evidence = GitHubPostChangeEvidenceProvider(FakeTransport()).collect(request())
    assert evidence.snapshot.entries[0].content_sha256 == request().expected_files[0].content_sha256
    assert evidence.snapshot.entries[0].status is SnapshotFileStatus.MODIFIED


def test_duplicate_push_and_pr_ci_runs_are_aggregated():
    evidence = GitHubPostChangeEvidenceProvider(FakeTransport()).collect(request())
    assert len(evidence.tests.checks) == 1
    assert evidence.tests.checks[0].name == "CI"
    assert evidence.tests.checks[0].conclusion is CheckConclusion.SUCCESS
    assert evidence.tests.checks[0].event == "pull_request"


def test_failed_duplicate_ci_run_fails_closed():
    transport = FakeTransport()
    transport.runs[0] = {**transport.runs[0], "conclusion": "failure"}
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert evidence.tests.checks[0].conclusion is CheckConclusion.FAILURE


def test_old_sha_ci_run_is_ignored():
    transport = FakeTransport()
    transport.runs.append({
        "id": 12, "name": "CI", "event": "push", "status": "completed",
        "conclusion": "success", "head_sha": OTHER,
    })
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert len(evidence.tests.checks) == 1


def test_truncated_git_tree_blocks_snapshot():
    transport = FakeTransport()
    transport.tree = {"truncated": True, "tree": []}
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert evidence.snapshot is not None
    assert evidence.snapshot.complete is False
    assert evidence.snapshot.truncated is True


def test_deleted_file_cannot_be_verified_as_complete():
    transport = FakeTransport()
    transport.files = [{"filename": "app.py", "status": "removed"}]
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert evidence.snapshot is not None
    assert evidence.snapshot.complete is False


def test_binary_file_is_marked_binary():
    transport = FakeTransport()
    transport.contents["app.py"] = b"\xff\x00\x01"
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert evidence.snapshot.entries[0].is_binary is True


def test_rename_is_preserved_for_phase6_rejection():
    transport = FakeTransport()
    transport.files = [{
        "filename": "new.py",
        "previous_filename": "old.py",
        "status": "renamed",
    }]
    transport.tree["tree"] = [{
        "path": "new.py", "mode": "100644", "type": "blob", "sha": "x"
    }]
    transport.contents["new.py"] = b"print('new')\n"
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert evidence.snapshot.entries[0].previous_path == "old.py"


def test_missing_pr_fields_fail_closed():
    transport = FakeTransport()

    def broken_get(repository, pull_request):
        return {"number": 7}

    transport.get = broken_get
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert evidence.pull_request is None


def test_missing_branch_head_fails_closed_for_commit_identity():
    transport = FakeTransport()
    transport.head_sha = lambda repository, branch: None
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert evidence.commit is None


def test_malformed_pr_metadata_is_rejected_not_defaulted():
    transport = FakeTransport()

    def malformed(repository, pull_request):
        value = FakeTransport().get(repository, pull_request)
        value["head"]["repo"]["full_name"] = None
        return value

    transport.get = malformed
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert evidence.pull_request is None


def test_paginated_pull_files_exhaustion_is_incomplete():
    transport = FakeTransport()
    transport.files = [{"filename": "app.py", "status": "modified"}] * 100
    original = transport.pull_request_files
    calls = []

    def full_pages(repository, pull_request, *, page=1, per_page=100):
        calls.append(page)
        return original(repository, pull_request, page=page, per_page=per_page)

    transport.pull_request_files = full_pages
    evidence = GitHubPostChangeEvidenceProvider(transport).collect(request())
    assert calls == [1, 2, 3, 4, 5, 6]
    assert evidence.snapshot.complete is False


def test_verify_runs_pure_phase6_gate():
    report = GitHubPostChangeEvidenceProvider(FakeTransport()).verify(request())
    assert report.verdict.value == "pass"
