from __future__ import annotations

import ast
import dataclasses
import inspect
from enum import Enum
from pathlib import Path

import pytest

import autonomous_agent.post_change_evidence as evidence_module
import autonomous_agent.post_change_final as final_module
import autonomous_agent.post_change_snapshot as snapshot_module
import autonomous_agent.post_change_verification as core_module
from autonomous_agent.post_change_final import Phase6Evidence, verify_phase6
from autonomous_agent.post_change_evidence import CommitObservation, PullRequestObservation
from autonomous_agent.post_change_snapshot import SnapshotEntry, SnapshotObservation
from autonomous_agent.post_change_verification import (
    FileManifestEntry, RequiredCheck, TestPolicy, VerificationRequest,
    Stage, StageState, Verdict,
)

SHA="a"*40
D_A="1"*64
REPO="Pappu246/autonomous-ai-scout"

def req():
    return VerificationRequest(
        repository=REPO,pull_request_number=170,
        expected_head_branch="arena/m5",expected_base_branch="main",
        expected_commit_sha=SHA,
        expected_files=(FileManifestEntry("a.py",D_A),),
        test_policy=TestPolicy((RequiredCheck("CI / test"),)),
    )

def commit():
    return CommitObservation(REPO,SHA,"arena/m5","main",True)

def pr():
    return PullRequestObservation(REPO,170,"open",False,False,REPO,"arena/m5","main",SHA,True)

def snap():
    return SnapshotObservation(REPO,SHA,(SnapshotEntry("a.py",D_A),),True,False,1)

def forbidden_call_names(tree):
    names=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func,ast.Attribute):
                names.add(node.func.attr)
    return names

def production_modules():
    return [core_module,evidence_module,snapshot_module,final_module]

def test_phase6_modules_import_only_safe_stdlib_and_local_phase6_modules():
    allowed_top={"__future__","dataclasses","enum","hashlib","json","re","typing"}
    allowed_local={"autonomous_agent.post_change_verification","autonomous_agent.post_change_evidence","autonomous_agent.post_change_snapshot"}
    for module in production_modules():
        tree=ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node,ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in allowed_top
            elif isinstance(node,ast.ImportFrom):
                assert node.module
                root=node.module.split(".")[0]
                if node.level:
                    assert node.module in {
                        "post_change_verification",
                        "post_change_evidence",
                        "post_change_snapshot",
                    }
                else:
                    assert root in allowed_top or node.module in allowed_local

def test_phase6_modules_contain_no_execution_or_transport_calls():
    forbidden={"open","system","popen","execv","spawn","fork","eval","exec",
               "__import__","Popen","check_output"}
    for module in production_modules():
        tree=ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        calls=forbidden_call_names(tree)
        assert calls.isdisjoint(forbidden), (module.__name__, sorted(calls & forbidden))

def test_phase6_surface_has_no_mutation_or_delivery_methods():
    for module in production_modules():
        for name,obj in inspect.getmembers(module):
            if inspect.isclass(obj) and obj.__module__ == module.__name__:
                if issubclass(obj, Enum):
                    continue
                forbidden = {
                    attr
                    for attr in dir(obj)
                    if not attr.startswith("_")
                    and callable(getattr(obj, attr, None))
                    and attr.lower().startswith(
                        ("send","merge","deploy","dispatch","delete","write","update","create","post","patch","request")
                    )
                }
                assert not forbidden, (module.__name__, name, sorted(forbidden))

def test_read_only_protocols_are_narrow():
    expected={
        evidence_module.ReadOnlyEvidenceProvider: {"read_commit_identity","read_pull_request"},
        snapshot_module.SnapshotReader: {"read_file_snapshot"},
        final_module.TestAttestationReader: {"read_completed_checks"},
    }
    for protocol,names in expected.items():
        actual={n for n,v in inspect.getmembers(protocol) if not n.startswith("_") and (inspect.isfunction(v) or inspect.ismethod(v))}
        assert actual == names

def test_exact_stage_order_is_fixed():
    report=verify_phase6(req(),Phase6Evidence(commit(),pr(),snap(),None))
    assert [item.stage for item in report.stages] == [
        Stage.COMMIT_IDENTITY,Stage.PULL_REQUEST_STATE,
        Stage.FILE_SNAPSHOT,Stage.POST_CHANGE_TESTS,Stage.FINAL_RESULT,
    ]

def test_missing_tests_can_never_pass():
    report=verify_phase6(req(),Phase6Evidence(commit(),pr(),snap(),None))
    assert report.verdict is not Verdict.PASS
    assert report.stage(Stage.POST_CHANGE_TESTS).state is StageState.MISSING

def test_all_phase6_reports_are_immutable():
    report=verify_phase6(req(),Phase6Evidence(commit(),pr(),snap(),None))
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.summary="tampered"

def test_snapshot_digest_is_canonical_and_deterministic():
    first=snap()
    second=SnapshotObservation(REPO,SHA,(SnapshotEntry("a.py",D_A),),True,False,1)
    assert first.canonical() == second.canonical()

def test_evidence_digest_changes_when_snapshot_changes():
    one=verify_phase6(req(),Phase6Evidence(commit(),pr(),snap(),None))
    changed=SnapshotObservation(REPO,SHA,(SnapshotEntry("a.py","2"*64),),True,False,1)
    two=verify_phase6(req(),Phase6Evidence(commit(),pr(),changed,None))
    assert one.evidence_digest != two.evidence_digest

def test_final_result_is_recomputed_not_input_supplied():
    report=verify_phase6(req(),Phase6Evidence(commit(),pr(),snap(),None))
    assert report.verdict is Verdict.BLOCKED

def test_no_phase6_module_reads_environment_or_secrets():
    for module in production_modules():
        source=Path(module.__file__).read_text(encoding="utf-8")
        assert "os.environ" not in source
        assert "getenv(" not in source
        assert "GITHUB_TOKEN" not in source
        assert "Authorization:" not in source
