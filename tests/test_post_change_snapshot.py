from __future__ import annotations

import dataclasses
import inspect

import pytest

from autonomous_agent.post_change_snapshot import (
    SnapshotEntry,
    SnapshotFailure,
    SnapshotFileStatus,
    SnapshotObservation,
    SnapshotPolicy,
    SnapshotReader,
    evaluate_file_snapshot,
)
from autonomous_agent.post_change_evidence import ReadOnlyVerificationState
from autonomous_agent.post_change_verification import (
    FileManifestEntry, RequiredCheck, TestPolicy, VerificationError, VerificationRequest,
)

SHA="a"*40
OTHER="b"*40
D_A="1"*64
D_B="2"*64

def request(files=("a.py",), digests=(D_A,)):
    return VerificationRequest(
        repository="Pappu246/autonomous-ai-scout", pull_request_number=170,
        expected_head_branch="arena/m3", expected_base_branch="main",
        expected_commit_sha=SHA,
        expected_files=tuple(FileManifestEntry(p,d) for p,d in zip(files,digests)),
        test_policy=TestPolicy((RequiredCheck("CI / test"),)),
    )

def entry(path="a.py", digest=D_A, **kw):
    return SnapshotEntry(path=path, content_sha256=digest, **kw)

def observation(entries=(entry(),), **kw):
    return SnapshotObservation(
        repository="Pappu246/autonomous-ai-scout",
        commit_sha=SHA, entries=tuple(entries), **kw)

def test_exact_snapshot_passes():
    assert evaluate_file_snapshot(request(), observation()).state is ReadOnlyVerificationState.PASS

def test_order_does_not_matter():
    req=request(files=("a.py","b.py"),digests=(D_A,D_B))
    obs=observation(entries=(entry("b.py",D_B),entry("a.py",D_A)))
    assert evaluate_file_snapshot(req,obs).state is ReadOnlyVerificationState.PASS

def test_wrong_repo_fails():
    obs=SnapshotObservation("other/repo",SHA,(entry(),))
    assert evaluate_file_snapshot(request(),obs).state is ReadOnlyVerificationState.FAIL

def test_wrong_sha_is_stale():
    obs=SnapshotObservation("Pappu246/autonomous-ai-scout",OTHER,(entry(),))
    assert evaluate_file_snapshot(request(),obs).state is ReadOnlyVerificationState.STALE

@pytest.mark.parametrize("kwargs",[
    {"complete":False},
    {"truncated":True},
    {"observed_file_count":2},
])
def test_incomplete_or_contradictory_snapshot_fails(kwargs):
    result=evaluate_file_snapshot(request(),observation(**kwargs))
    assert result.state in {ReadOnlyVerificationState.MISSING,ReadOnlyVerificationState.CONTRADICTORY}

def test_missing_and_unexpected_files_fail():
    req=request(files=("a.py","b.py"),digests=(D_A,D_B))
    assert evaluate_file_snapshot(req,observation()).state is ReadOnlyVerificationState.MISSING
    obs=observation(entries=(entry(),entry("b.py",D_B),entry("extra.py",D_A)))
    assert evaluate_file_snapshot(req,obs).state is ReadOnlyVerificationState.FAIL

@pytest.mark.parametrize("field",["is_symlink","is_submodule","is_binary"])
def test_disallowed_types_fail(field):
    assert evaluate_file_snapshot(request(),observation(entries=(entry(**{field:True}),))).state is ReadOnlyVerificationState.FAIL

def test_renamed_file_fails():
    result=evaluate_file_snapshot(request(),observation(entries=(entry(previous_path="old.py"),)))
    assert result.state is ReadOnlyVerificationState.FAIL
    assert result.failure is SnapshotFailure.RENAMED

def test_digest_mismatch_fails():
    assert evaluate_file_snapshot(request(),observation(entries=(entry(digest=D_B),))).state is ReadOnlyVerificationState.FAIL

@pytest.mark.parametrize("status",[SnapshotFileStatus.ADDED,SnapshotFileStatus.MODIFIED])
def test_allowed_statuses(status):
    assert evaluate_file_snapshot(request(),observation(entries=(entry(status=status),))).state is ReadOnlyVerificationState.PASS

def test_unsupported_status_fails():
    result=evaluate_file_snapshot(request(),observation(entries=(entry(status="removed"),)))
    assert result.state is ReadOnlyVerificationState.FAIL

def test_oversize_fails():
    result=evaluate_file_snapshot(request(),observation(entries=(entry(size_bytes=50*1024*1024+1),)))
    assert result.state is ReadOnlyVerificationState.FAIL

@pytest.mark.parametrize("path",["../evil.py","/absolute.py","a//b.py",""])
def test_unsafe_paths_are_rejected(path):
    with pytest.raises(VerificationError):
        SnapshotEntry(path=path, content_sha256=D_A)

def test_duplicate_paths_rejected():
    with pytest.raises(VerificationError):
        observation(entries=(entry(),entry()))

def test_snapshot_reader_is_one_method():
    names={n for n,v in inspect.getmembers(SnapshotReader) if inspect.isfunction(v) or inspect.ismethod(v)}
    assert names=={"read_file_snapshot"}
    assert not names.intersection({"write_file","delete_file","merge","post","patch","execute"})

def test_immutable_snapshot_models():
    model=entry()
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.path="x.py"

def test_observed_count_type_is_strict():
    with pytest.raises(VerificationError):
        observation(observed_file_count=True)
