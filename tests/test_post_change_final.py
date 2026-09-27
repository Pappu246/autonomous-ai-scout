from __future__ import annotations

import dataclasses
import inspect

import pytest

from autonomous_agent.post_change_final import (
    CheckConclusion,
    CheckObservation,
    Phase6Evidence,
    Phase6VerificationReport,
    TestAttestation,
    TestAttestationPolicy,
    TestAttestationReader,
    evaluate_post_change_tests,
    verify_phase6,
)
from autonomous_agent.post_change_evidence import CommitObservation, PullRequestObservation, ReadOnlyVerificationState
from autonomous_agent.post_change_snapshot import SnapshotEntry, SnapshotObservation
from autonomous_agent.post_change_verification import FileManifestEntry, RequiredCheck, TestPolicy, VerificationRequest, Verdict, Stage, StageState, VerificationError

SHA="a"*40
D_A="1"*64

def req():
    return VerificationRequest(
        repository="Pappu246/autonomous-ai-scout",pull_request_number=170,
        expected_head_branch="arena/m4",expected_base_branch="main",expected_commit_sha=SHA,
        expected_files=(FileManifestEntry("a.py",D_A),),
        test_policy=TestPolicy((RequiredCheck("CI / test"),)),
    )

def commit():
    return CommitObservation("Pappu246/autonomous-ai-scout",SHA,"arena/m4","main",True)

def pr():
    return PullRequestObservation("Pappu246/autonomous-ai-scout",170,"open",False,False,"Pappu246/autonomous-ai-scout","arena/m4","main",SHA,True)

def snap():
    return SnapshotObservation("Pappu246/autonomous-ai-scout",SHA,(SnapshotEntry("a.py",D_A),),True,False,1)

def check(**kw):
    data={"name":"CI / test","repository":"Pappu246/autonomous-ai-scout","head_sha":SHA,"event":"pull_request","status":"completed","conclusion":CheckConclusion.SUCCESS,"run_id":1,"completed":True,"workflow_name":"CI"}
    data.update(kw)
    return CheckObservation(**data)

def attest(checks=(check(),)):
    return TestAttestation("Pappu246/autonomous-ai-scout",SHA,tuple(checks))

def test_successful_exact_attestation():
    assert evaluate_post_change_tests(req(),attest()).state is ReadOnlyVerificationState.PASS

@pytest.mark.parametrize("kw",[
    {"head_sha":"b"*40},{"event":"schedule"},{"status":"queued","completed":False},
    {"conclusion":CheckConclusion.FAILURE},{"conclusion":CheckConclusion.NEUTRAL},
    {"conclusion":CheckConclusion.SKIPPED},{"conclusion":CheckConclusion.CANCELLED},
    {"conclusion":CheckConclusion.TIMED_OUT},{"conclusion":CheckConclusion.ACTION_REQUIRED},
    {"workflow_name":"Other"},
])
def test_non_success_or_untrusted_attestation_blocks(kw):
    result=evaluate_post_change_tests(req(),attest((check(**kw),)))
    assert result.state in {ReadOnlyVerificationState.FAIL,ReadOnlyVerificationState.BLOCKED,ReadOnlyVerificationState.STALE}

def test_missing_required_check_blocks():
    assert evaluate_post_change_tests(req(),attest(())).state is ReadOnlyVerificationState.MISSING

def test_wrong_repository_blocks():
    result=evaluate_post_change_tests(req(),attest((check(repository="other/repo"),)))
    assert result.state is ReadOnlyVerificationState.FAIL

def test_unexpected_check_is_malformed():
    result=evaluate_post_change_tests(req(),attest((check(name="CI / other"),)))
    assert result.state is ReadOnlyVerificationState.MALFORMED

def test_duplicate_checks_are_rejected():
    with pytest.raises(VerificationError):
        attest((check(),check()))

def test_reader_has_one_read_method():
    names={n for n,v in inspect.getmembers(TestAttestationReader) if inspect.isfunction(v) or inspect.ismethod(v)}
    assert names=={"read_completed_checks"}

def test_verify_phase6_success():
    evidence=Phase6Evidence(commit(),pr(),snap(),attest())
    report=verify_phase6(req(),evidence)
    assert report.verdict is Verdict.PASS
    assert [s.stage for s in report.stages]==[Stage.COMMIT_IDENTITY,Stage.PULL_REQUEST_STATE,Stage.FILE_SNAPSHOT,Stage.POST_CHANGE_TESTS,Stage.FINAL_RESULT]
    assert all(s.state is StageState.PASS for s in report.stages)

@pytest.mark.parametrize("which",["commit","pull_request","snapshot","tests"])
def test_earlier_failure_blocks_later(which):
    evidence=Phase6Evidence(commit(),pr(),snap(),attest())
    if which=="commit": evidence=dataclasses.replace(evidence,commit=CommitObservation("other/repo",SHA,"arena/m4","main",True))
    elif which=="pull_request": evidence=dataclasses.replace(evidence,pull_request=dataclasses.replace(pr(),draft=True))
    elif which=="snapshot": evidence=dataclasses.replace(evidence,snapshot=dataclasses.replace(snap(),commit_sha="b"*40))
    else: evidence=dataclasses.replace(evidence,tests=None)
    report=verify_phase6(req(),evidence)
    stages={s.stage:s for s in report.stages}
    if which=="commit":
        assert stages[Stage.PULL_REQUEST_STATE].evaluated is False
        assert stages[Stage.FILE_SNAPSHOT].evaluated is False
        assert stages[Stage.POST_CHANGE_TESTS].evaluated is False
    elif which=="pull_request":
        assert stages[Stage.FILE_SNAPSHOT].evaluated is False
        assert stages[Stage.POST_CHANGE_TESTS].evaluated is False
    elif which=="snapshot":
        assert stages[Stage.POST_CHANGE_TESTS].evaluated is False
    assert report.verdict is not Verdict.PASS

def test_tamper_changes_evidence_digest():
    base=Phase6Evidence(commit(),pr(),snap(),attest())
    one=verify_phase6(req(),base)
    two=verify_phase6(req(),dataclasses.replace(base,tests=attest((check(event="push"),))))
    assert one.evidence_digest != two.evidence_digest

def test_policy_digest_changes_when_policy_changes():
    evidence=Phase6Evidence(commit(),pr(),snap(),attest())
    one=verify_phase6(req(),evidence, test_policy=TestAttestationPolicy(allowed_events=frozenset({"pull_request"})))
    two=verify_phase6(req(),evidence, test_policy=TestAttestationPolicy(allowed_events=frozenset({"push"})))
    assert one.policy_digest != two.policy_digest

def test_report_is_frozen():
    report=verify_phase6(req(),Phase6Evidence(commit(),pr(),snap(),attest()))
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.verdict=Verdict.FAIL
