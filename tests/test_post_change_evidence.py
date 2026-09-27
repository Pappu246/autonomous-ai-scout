from __future__ import annotations

import dataclasses
import inspect
from typing import get_type_hints

import pytest

from autonomous_agent.post_change_evidence import (
    CommitIdentityReader,
    CommitObservation,
    PRReviewability,
    PullRequestObservation,
    PullRequestReader,
    ReadOnlyEvidenceProvider,
    ReadOnlyVerificationResult,
    ReadOnlyVerificationState,
    evaluate_read_only_identity,
    evaluate_read_only_pr,
)
from autonomous_agent.post_change_verification import (
    FileManifestEntry,
    RequiredCheck,
    TestPolicy,
    VerificationError,
    VerificationRequest,
)

SHA = "a" * 40
OTHER_SHA = "b" * 40
D_A = "1" * 64

def request() -> VerificationRequest:
    return VerificationRequest(
        repository="Pappu246/autonomous-ai-scout",
        pull_request_number=170,
        expected_head_branch="arena/m2",
        expected_base_branch="main",
        expected_commit_sha=SHA,
        expected_files=(FileManifestEntry("a.py", D_A),),
        test_policy=TestPolicy((RequiredCheck("CI / test"),)),
    )

def identity(**overrides) -> CommitObservation:
    data = {
        "repository": "Pappu246/autonomous-ai-scout",
        "commit_sha": SHA,
        "head_branch": "arena/m2",
        "base_branch": "main",
        "head_present": True,
    }
    data.update(overrides)
    return CommitObservation(**data)

def pr(**overrides) -> PullRequestObservation:
    data = {
        "repository": "Pappu246/autonomous-ai-scout",
        "number": 170,
        "state": "open",
        "draft": False,
        "merged": False,
        "head_repository": "Pappu246/autonomous-ai-scout",
        "head_branch": "arena/m2",
        "base_branch": "main",
        "head_commit_sha": SHA,
        "head_present": True,
    }
    data.update(overrides)
    return PullRequestObservation(**data)

def test_identity_exact_match_passes():
    result = evaluate_read_only_identity(request(), identity())
    assert result.state is ReadOnlyVerificationState.PASS

@pytest.mark.parametrize("field,value", [
    ("repository", "other/repo"),
    ("commit_sha", OTHER_SHA),
    ("head_branch", "other/head"),
    ("base_branch", "other/base"),
    ("head_present", False),
])
def test_identity_mismatch_fails(field, value):
    result = evaluate_read_only_identity(request(), identity(**{field:value}))
    assert result.state is ReadOnlyVerificationState.FAIL

@pytest.mark.parametrize("number", [0, -1, True, "170"])
def test_pr_number_is_strict(number):
    with pytest.raises(VerificationError):
        PullRequestObservation(
            repository="Pappu246/autonomous-ai-scout", number=number,
            state="open", draft=False, merged=False,
            head_repository="Pappu246/autonomous-ai-scout",
            head_branch="arena/m2", base_branch="main",
            head_commit_sha=SHA,
        )

@pytest.mark.parametrize("state", ["closed", "locked", "merged", "unknown"])
def test_non_open_prs_are_not_reviewable(state):
    observation = pr(state="closed" if state == "merged" else state, merged=(state=="merged"))
    result = evaluate_read_only_pr(request(), observation, identity())
    assert result.state is ReadOnlyVerificationState.FAIL

def test_draft_is_distinct_and_blocked():
    observation = pr(draft=True)
    result = evaluate_read_only_pr(request(), observation, identity())
    assert result.state is ReadOnlyVerificationState.FAIL
    assert result.reviewability is PRReviewability.DRAFT

@pytest.mark.parametrize("field,value", [
    ("repository", "other/repo"),
    ("number", 171),
    ("head_repository", "other/repo"),
    ("head_branch", "other/head"),
    ("base_branch", "other/base"),
    ("head_commit_sha", OTHER_SHA),
    ("head_present", False),
])
def test_pr_identity_mismatch_fails(field, value):
    result = evaluate_read_only_pr(request(), pr(**{field:value}), identity())
    assert result.state in {
        ReadOnlyVerificationState.FAIL,
        ReadOnlyVerificationState.STALE,
        ReadOnlyVerificationState.CONTRADICTORY,
    }

def test_identity_failure_blocks_pr():
    bad = identity(commit_sha=OTHER_SHA)
    result = evaluate_read_only_pr(request(), pr(), bad)
    assert result.state is ReadOnlyVerificationState.BLOCKED
    assert result.evaluated is False

def test_malformed_boolean_is_rejected():
    with pytest.raises(VerificationError):
        PullRequestObservation(
            repository="Pappu246/autonomous-ai-scout", number=170,
            state="open", draft="false", merged=False,
            head_repository="Pappu246/autonomous-ai-scout",
            head_branch="arena/m2", base_branch="main", head_commit_sha=SHA,
        )

def test_contradictory_merged_state_is_rejected():
    with pytest.raises(VerificationError):
        PullRequestObservation(
            repository="Pappu246/autonomous-ai-scout", number=170,
            state="open", draft=False, merged=True,
            head_repository="Pappu246/autonomous-ai-scout",
            head_branch="arena/m2", base_branch="main", head_commit_sha=SHA,
        )

def test_provider_surface_is_narrow_and_read_only():
    methods = {
        name for name, value in inspect.getmembers(ReadOnlyEvidenceProvider)
        if not name.startswith("_") and (inspect.isfunction(value) or inspect.ismethod(value))
    }
    assert methods == {"read_commit_identity", "read_pull_request"}
    forbidden = {"request", "post", "patch", "delete", "merge", "dispatch",
                 "write_file", "create_branch", "update_branch", "execute"}
    assert methods.isdisjoint(forbidden)

def test_provider_protocol_has_no_credentials_or_transport_parameters():
    for protocol in (CommitIdentityReader, PullRequestReader, ReadOnlyEvidenceProvider):
        for name, member in inspect.getmembers(protocol):
            if name.startswith("_") or not callable(member):
                continue
            hints = get_type_hints(member)
            assert "str" not in {str(v) for v in hints.values() if v is not str}
            signature = inspect.signature(member)
            assert "url" not in signature.parameters
            assert "headers" not in signature.parameters
            assert "token" not in signature.parameters
            assert "callback" not in signature.parameters

def test_immutable_results():
    result = evaluate_read_only_identity(request(), identity())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.state = ReadOnlyVerificationState.FAIL

@pytest.mark.parametrize("value", [None, {}, [], "x", 1])
def test_bad_identity_evidence_fails_closed_or_is_rejected(value):
    if value is None:
        result = evaluate_read_only_identity(request(), None)
        assert result.state is ReadOnlyVerificationState.MISSING
        return
    with pytest.raises(VerificationError):
        evaluate_read_only_identity(request(), value)  # type: ignore[arg-type]
