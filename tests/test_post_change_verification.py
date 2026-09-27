"""Phase 6 M1 tests — pure verification contract and decision core."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import re
import sys
from pathlib import Path

import pytest

from autonomous_agent import post_change_verification as pcv
from autonomous_agent.post_change_verification import (
    MAX_DETAIL_CHARS,
    STAGE_ORDER,
    CheckOutcome,
    CommitIdentityEvidence,
    FileManifestEntry,
    FileSnapshotEntry,
    FileSnapshotEvidence,
    PostChangeTestEvidence,
    PullRequestEvidence,
    RequiredCheck,
    Stage,
    StageState,
    TestPolicy,
    VerificationError,
    VerificationEvidence,
    VerificationRequest,
    Verdict,
    canonical_json,
    digest_of,
    evaluate_verification,
)

SHA = "a" * 40
OTHER_SHA = "b" * 40
REPO = "Pappu246/autonomous-ai-scout"
HEAD = "arena/phase6-m1"
BASE = "main"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


D_A = _digest("alpha")
D_B = _digest("beta")


def make_request(**overrides):
    payload = {
        "repository": REPO,
        "pull_request_number": 170,
        "expected_head_branch": HEAD,
        "expected_base_branch": BASE,
        "expected_commit_sha": SHA,
        "expected_files": (
            FileManifestEntry("autonomous_agent/post_change_verification.py", D_A),
            FileManifestEntry("tests/test_post_change_verification.py", D_B),
        ),
        "test_policy": TestPolicy(checks=(RequiredCheck("pytest"), RequiredCheck("lint"))),
    }
    payload.update(overrides)
    return VerificationRequest(**payload)


def make_evidence(**overrides) -> VerificationEvidence:
    payload = {
        "commit_identity": CommitIdentityEvidence(SHA, HEAD, BASE),
        "pull_request": PullRequestEvidence(170, "open", HEAD, BASE, SHA),
        "file_snapshot": FileSnapshotEvidence(
            SHA,
            (
                FileSnapshotEntry("autonomous_agent/post_change_verification.py", D_A),
                FileSnapshotEntry("tests/test_post_change_verification.py", D_B),
            ),
        ),
        "post_change_tests": PostChangeTestEvidence(
            SHA, (CheckOutcome("pytest", True, "ok"), CheckOutcome("lint", True, "ok"))
        ),
    }
    payload.update(overrides)
    return VerificationEvidence(**payload)


# 1 -------------------------------------------------------------------------
def test_valid_request_produces_five_stages_in_exact_order():
    report = evaluate_verification(make_request(), make_evidence())
    assert [s.stage for s in report.stages] == list(STAGE_ORDER)
    assert [s.stage.value for s in report.stages] == [
        "commit_identity",
        "pull_request_state",
        "file_snapshot",
        "post_change_tests",
        "final_result",
    ]
    assert report.verdict is Verdict.PASS
    assert report.passed is True


# 2 -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "stage,evidence_override",
    [
        (Stage.COMMIT_IDENTITY, {"commit_identity": CommitIdentityEvidence(OTHER_SHA, HEAD, BASE)}),
        (Stage.PULL_REQUEST_STATE, {"pull_request": PullRequestEvidence(170, "closed", HEAD, BASE, SHA)}),
        (Stage.FILE_SNAPSHOT, {"file_snapshot": FileSnapshotEvidence(SHA, ())}),
        (
            Stage.POST_CHANGE_TESTS,
            {
                "post_change_tests": PostChangeTestEvidence(
                    SHA, (CheckOutcome("pytest", False), CheckOutcome("lint", True))
                )
            },
        ),
    ],
)
def test_each_stage_failure_blocks_final_verdict(stage, evidence_override):
    report = evaluate_verification(make_request(), make_evidence(**evidence_override))
    assert report.verdict is not Verdict.PASS
    assert report.passed is False
    assert report.stage(stage).state is not StageState.PASS
    assert report.stage(Stage.FINAL_RESULT).state in {StageState.FAIL, StageState.BLOCKED}


# 3 -------------------------------------------------------------------------
@pytest.mark.parametrize("repository", [None, "", "   ", 5, True, "no-slash", "a" * 300])
def test_missing_or_invalid_repository_fails_closed(repository):
    with pytest.raises(VerificationError):
        make_request(repository=repository)


def test_request_mapping_missing_repository_fails_closed():
    with pytest.raises(VerificationError):
        VerificationRequest.from_mapping(
            {
                "pull_request_number": 1,
                "expected_head_branch": HEAD,
                "expected_base_branch": BASE,
                "expected_commit_sha": SHA,
                "expected_files": [{"path": "a.py", "content_sha256": D_A}],
                "test_policy": {"checks": [{"name": "pytest"}]},
            }
        )


# 4 -------------------------------------------------------------------------
@pytest.mark.parametrize("number", [None, 0, -1, True, False, "170", 1.0, 10**9])
def test_missing_or_invalid_pr_number_fails_closed(number):
    with pytest.raises(VerificationError):
        make_request(pull_request_number=number)


# 5 / 6 / 7 ----------------------------------------------------------------
@pytest.mark.parametrize(
    "sha",
    [
        None,
        "",
        "a" * 39,
        "a" * 41,
        "abc1234",  # abbreviated
        "g" * 40,  # non-hex
        "A" * 39 + "z",
        123,
        True,
        ["a" * 40],
    ],
)
def test_missing_malformed_abbreviated_or_non_hex_sha_fails_closed(sha):
    with pytest.raises(VerificationError):
        make_request(expected_commit_sha=sha)


def test_uppercase_sha_is_canonically_normalized():
    request = make_request(expected_commit_sha="A" * 40)
    assert request.expected_commit_sha == "a" * 40


# 8 -------------------------------------------------------------------------
@pytest.mark.parametrize("branch", [None, "", "  ", 7, True, "bad branch", "-leading", "x" * 300])
def test_invalid_branches_fail_closed(branch):
    with pytest.raises(VerificationError):
        make_request(expected_head_branch=branch)
    with pytest.raises(VerificationError):
        make_request(expected_base_branch=branch)


def test_identical_head_and_base_branch_rejected():
    with pytest.raises(VerificationError):
        make_request(expected_head_branch="main", expected_base_branch="main")


# 9 -------------------------------------------------------------------------
@pytest.mark.parametrize("manifest", [None, (), [], "a.py", {"a.py": D_A}, 5])
def test_missing_or_invalid_file_manifest_fails_closed(manifest):
    with pytest.raises(VerificationError):
        make_request(expected_files=manifest)


def test_manifest_is_bounded():
    entries = tuple(
        FileManifestEntry(f"pkg/mod{i}.py", D_A) for i in range(pcv.MAX_MANIFEST_ENTRIES + 1)
    )
    with pytest.raises(VerificationError):
        make_request(expected_files=entries)


def test_duplicate_manifest_paths_rejected():
    with pytest.raises(VerificationError):
        make_request(expected_files=(FileManifestEntry("a.py", D_A), FileManifestEntry("a.py", D_B)))


# 10 ------------------------------------------------------------------------
@pytest.mark.parametrize("policy", [None, (), [], "pytest", 3, {"checks": []}])
def test_missing_or_invalid_test_policy_fails_closed(policy):
    with pytest.raises(VerificationError):
        make_request(test_policy=policy)


def test_test_policy_is_bounded():
    with pytest.raises(VerificationError):
        TestPolicy(checks=tuple(RequiredCheck(f"check{i}") for i in range(pcv.MAX_REQUIRED_CHECKS + 1)))


def test_policy_requires_at_least_one_required_check():
    with pytest.raises(VerificationError):
        TestPolicy(checks=(RequiredCheck("pytest", required=False),))


# 11 ------------------------------------------------------------------------
@pytest.mark.parametrize("truthy", ["true", "True", 1, "yes", [1], {"ok": 1}, 1.0])
def test_non_boolean_values_cannot_satisfy_a_passing_stage(truthy):
    with pytest.raises(VerificationError):
        CheckOutcome("pytest", truthy)
    with pytest.raises(VerificationError):
        FileSnapshotEvidence(SHA, (), complete=truthy)
    with pytest.raises(VerificationError):
        CommitIdentityEvidence(SHA, HEAD, BASE, resolved=truthy)


def test_truthy_check_outcome_via_mapping_is_rejected():
    with pytest.raises(VerificationError):
        PostChangeTestEvidence.from_mapping(
            {
                "at_commit_sha": SHA,
                "outcomes": [{"name": "pytest", "passed": "true"}],
                "complete": True,
            }
        )


# 12 ------------------------------------------------------------------------
def test_no_provider_or_callback_mechanism_exists_in_pure_core():
    for forbidden in (
        "VerificationBackend",
        "run_tests",
        "get_head_commit",
        "get_pr_state",
        "read_file",
        "fetch",
    ):
        assert not hasattr(pcv, forbidden)

    signature = inspect.signature(evaluate_verification)
    assert list(signature.parameters) == ["request", "evidence"]
    for parameter in signature.parameters.values():
        assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameter.default is inspect.Parameter.empty
        assert "Callable" not in str(parameter.annotation)

    source = Path(pcv.__file__).read_text(encoding="utf-8")
    assert "Protocol" not in source
    assert "Callable" not in source

    def provider():  # a provider-style object must not be accepted anywhere
        raise AssertionError("provider must never be invoked")

    with pytest.raises(VerificationError):
        evaluate_verification(provider, make_evidence())
    report = evaluate_verification(make_request(), provider)
    assert report.verdict is Verdict.BLOCKED


# 13 ------------------------------------------------------------------------
def test_identity_failure_prevents_evaluation_of_later_evidence_stages():
    report = evaluate_verification(
        make_request(),
        make_evidence(commit_identity=CommitIdentityEvidence(OTHER_SHA, HEAD, BASE)),
    )
    assert report.stage(Stage.COMMIT_IDENTITY).state is StageState.FAIL
    for stage in (Stage.PULL_REQUEST_STATE, Stage.FILE_SNAPSHOT, Stage.POST_CHANGE_TESTS):
        result = report.stage(stage)
        assert result.state is StageState.BLOCKED
        assert result.evaluated is False
        assert "commit identity" in result.detail
    assert report.verdict is Verdict.FAIL


def test_missing_identity_evidence_blocks_all_later_stages():
    report = evaluate_verification(make_request(), make_evidence(commit_identity=None))
    assert report.stage(Stage.COMMIT_IDENTITY).state is StageState.MISSING
    assert all(
        report.stage(stage).evaluated is False
        for stage in (Stage.PULL_REQUEST_STATE, Stage.FILE_SNAPSHOT, Stage.POST_CHANGE_TESTS)
    )
    assert report.verdict is Verdict.BLOCKED


# 14 ------------------------------------------------------------------------
def test_result_detail_is_bounded():
    long_paths = tuple(
        FileManifestEntry(f"pkg/{'d' * 100}/module_number_{i}.py", D_A) for i in range(40)
    )
    request = make_request(expected_files=long_paths)
    report = evaluate_verification(request, make_evidence(file_snapshot=FileSnapshotEvidence(SHA, ())))
    for stage in report.stages:
        assert len(stage.detail) <= MAX_DETAIL_CHARS
    assert len(report.summary) <= MAX_DETAIL_CHARS
    assert len(canonical_json(report)) < 10_000


def test_check_outcome_detail_is_bounded():
    outcome = CheckOutcome("pytest", False, "x" * 5000)
    assert len(outcome.detail) <= MAX_DETAIL_CHARS


# 15 ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "secret",
    [
        "Authorization: Bearer abcdef1234567890abcdef",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "api_key=supersecretvalue123",
        "GITHUB_TOKEN=ghs_abcdefghijklmnopqrstuvwxyz",
        "password=hunter2hunter2",
        "sk-abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_result_detail_is_redacted(secret):
    outcome = CheckOutcome("pytest", False, f"failed: {secret} while running")
    assert "[REDACTED]" in outcome.detail
    for fragment in ("ghp_", "hunter2", "supersecret", "Bearer abcdef", "sk-abcdefghij", "ghs_"):
        assert fragment not in outcome.detail

    report = evaluate_verification(
        make_request(),
        make_evidence(
            post_change_tests=PostChangeTestEvidence(
                SHA, (CheckOutcome("pytest", False, secret), CheckOutcome("lint", True))
            )
        ),
    )
    serialized = canonical_json(report)
    for fragment in ("ghp_", "hunter2", "supersecret", "sk-abcdefghij", "ghs_"):
        assert fragment not in serialized


# 16 ------------------------------------------------------------------------
def test_deeply_nested_hostile_input_is_rejected_without_unbounded_recursion():
    hostile: object = "leaf"
    for _ in range(2000):
        hostile = {"nested": hostile}
    with pytest.raises(VerificationError):
        VerificationRequest.from_mapping({"repository": hostile})
    with pytest.raises(VerificationError):
        VerificationEvidence.from_mapping({"commit_identity": hostile})

    deep_list: object = ["leaf"]
    for _ in range(2000):
        deep_list = [deep_list]
    with pytest.raises(VerificationError):
        VerificationEvidence.from_mapping({"file_snapshot": deep_list})


def test_self_referential_input_is_rejected():
    hostile: dict = {}
    hostile["commit_identity"] = hostile
    with pytest.raises(VerificationError):
        VerificationEvidence.from_mapping(hostile)


# 17 ------------------------------------------------------------------------
def test_unknown_fields_are_rejected_by_strict_models():
    base = {
        "repository": REPO,
        "pull_request_number": 170,
        "expected_head_branch": HEAD,
        "expected_base_branch": BASE,
        "expected_commit_sha": SHA,
        "expected_files": [{"path": "a.py", "content_sha256": D_A}],
        "test_policy": {"checks": [{"name": "pytest"}]},
    }
    assert VerificationRequest.from_mapping(base).repository == REPO

    with pytest.raises(VerificationError):
        VerificationRequest.from_mapping({**base, "force_pass": True})
    with pytest.raises(VerificationError):
        FileManifestEntry.from_mapping({"path": "a.py", "content_sha256": D_A, "mode": "0755"})
    with pytest.raises(VerificationError):
        CheckOutcome.from_mapping({"name": "pytest", "passed": True, "verdict": "pass"})
    with pytest.raises(VerificationError):
        VerificationEvidence.from_mapping({"final_verdict": "pass"})


# 18 ------------------------------------------------------------------------
def test_canonical_representation_and_digest_are_deterministic():
    first = make_request()
    second = make_request()
    assert first is not second
    assert canonical_json(first) == canonical_json(second)
    assert digest_of(first) == digest_of(second)
    assert len(digest_of(first)) == 64

    report_a = evaluate_verification(first, make_evidence())
    report_b = evaluate_verification(second, make_evidence())
    assert canonical_json(report_a) == canonical_json(report_b)
    assert digest_of(report_a) == digest_of(report_b)
    assert report_a.request_digest == report_b.request_digest
    assert report_a.evidence_digest == report_b.evidence_digest

    changed = make_request(pull_request_number=171)
    assert digest_of(changed) != digest_of(first)


# 19 ------------------------------------------------------------------------
def test_reordered_unordered_manifest_entries_do_not_change_digest():
    entries = (
        FileManifestEntry("a.py", D_A),
        FileManifestEntry("b.py", D_B),
    )
    forward = make_request(expected_files=entries)
    reversed_request = make_request(expected_files=tuple(reversed(entries)))
    assert digest_of(forward) == digest_of(reversed_request)

    ordered_policy = TestPolicy(checks=(RequiredCheck("pytest"),), manifest_unordered=False)
    ordered_forward = make_request(expected_files=entries, test_policy=ordered_policy)
    ordered_reversed = make_request(
        expected_files=tuple(reversed(entries)), test_policy=ordered_policy
    )
    assert digest_of(ordered_forward) != digest_of(ordered_reversed)


def test_digest_never_depends_on_object_identity():
    request = make_request()
    assert digest_of(request) == digest_of(make_request())
    assert str(id(request)) not in canonical_json(request)


# 20 ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "instance,attribute,value",
    [
        (make_request(), "repository", "other/repo"),
        (make_request(), "expected_commit_sha", OTHER_SHA),
        (FileManifestEntry("a.py", D_A), "path", "b.py"),
        (RequiredCheck("pytest"), "required", False),
        (CheckOutcome("pytest", False), "passed", True),
        (CommitIdentityEvidence(SHA, HEAD, BASE), "observed_commit_sha", OTHER_SHA),
        (PullRequestEvidence(170, "open", HEAD, BASE, SHA), "state", "merged"),
    ],
)
def test_mutating_frozen_models_is_rejected(instance, attribute, value):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, attribute, value)


def test_report_and_stage_results_are_frozen():
    report = evaluate_verification(make_request(), make_evidence())
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.verdict = Verdict.PASS
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.stages[0].state = StageState.PASS
    assert isinstance(report.stages, tuple)


# 21 ------------------------------------------------------------------------
def test_final_verdict_cannot_be_supplied_by_input_evidence():
    assert not any(
        f.name in {"verdict", "passed", "final_result", "final_verdict"}
        for f in dataclasses.fields(VerificationEvidence)
    )
    with pytest.raises(VerificationError):
        VerificationEvidence.from_mapping(
            {"verdict": "pass", "commit_identity": CommitIdentityEvidence(SHA, HEAD, BASE)}
        )
    with pytest.raises(TypeError):
        VerificationEvidence(verdict="pass")  # type: ignore[call-arg]


# 22 ------------------------------------------------------------------------
def test_forged_passed_true_cannot_override_failed_structured_evidence():
    with pytest.raises(VerificationError):
        PostChangeTestEvidence.from_mapping(
            {
                "at_commit_sha": SHA,
                "outcomes": [{"name": "pytest", "passed": False}],
                "complete": True,
                "passed": True,
            }
        )

    report = evaluate_verification(
        make_request(),
        make_evidence(
            post_change_tests=PostChangeTestEvidence(
                SHA,
                (
                    CheckOutcome("pytest", False, "1 failed"),
                    CheckOutcome("lint", True, "ok"),
                ),
            )
        ),
    )
    assert report.verdict is Verdict.FAIL
    assert report.stage(Stage.POST_CHANGE_TESTS).state is StageState.FAIL
    assert report.stage(Stage.FINAL_RESULT).state is StageState.FAIL


def test_unreported_required_check_cannot_pass():
    report = evaluate_verification(
        make_request(),
        make_evidence(
            post_change_tests=PostChangeTestEvidence(SHA, (CheckOutcome("pytest", True),))
        ),
    )
    assert report.stage(Stage.POST_CHANGE_TESTS).state is StageState.MISSING
    assert report.verdict is Verdict.BLOCKED


# 23 ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "evidence",
    [
        None,
        VerificationEvidence(),
        {},
        {"commit_identity": {"observed_commit_sha": "nope"}},
        "evidence",
        42,
        [1, 2, 3],
    ],
)
def test_empty_or_malformed_evidence_fails_closed(evidence):
    report = evaluate_verification(make_request(), evidence)
    assert report.verdict is Verdict.BLOCKED
    assert report.passed is False
    assert report.stage(Stage.COMMIT_IDENTITY).state in {
        StageState.MISSING,
        StageState.MALFORMED,
    }
    assert [s.stage for s in report.stages] == list(STAGE_ORDER)


def test_stale_and_contradictory_evidence_cannot_pass():
    stale = evaluate_verification(
        make_request(), make_evidence(file_snapshot=FileSnapshotEvidence(OTHER_SHA, ()))
    )
    assert stale.stage(Stage.FILE_SNAPSHOT).state is StageState.STALE
    assert stale.verdict is Verdict.BLOCKED

    contradictory = evaluate_verification(
        make_request(),
        make_evidence(pull_request=PullRequestEvidence(999, "open", HEAD, BASE, SHA)),
    )
    assert contradictory.stage(Stage.PULL_REQUEST_STATE).state is StageState.CONTRADICTORY
    assert contradictory.verdict is Verdict.BLOCKED

    duplicate = evaluate_verification(
        make_request(),
        make_evidence(
            post_change_tests=PostChangeTestEvidence(
                SHA,
                (
                    CheckOutcome("pytest", True),
                    CheckOutcome("pytest", False),
                    CheckOutcome("lint", True),
                ),
            )
        ),
    )
    assert duplicate.stage(Stage.POST_CHANGE_TESTS).state is StageState.CONTRADICTORY


# 24 ------------------------------------------------------------------------
def test_decision_function_performs_no_filesystem_network_or_process_activity():
    forbidden_prefixes = (
        "open",
        "socket.",
        "subprocess.",
        "os.system",
        "os.exec",
        "os.spawn",
        "os.posix_spawn",
        "os.fork",
        "urllib.",
        "http.client",
        "shutil.",
        "pickle.",
        "ctypes.",
        "exec",
        "eval",
        "compile",
        "import",
    )
    observed: list[str] = []

    def hook(event: str, args):  # pragma: no cover - only records
        if event.startswith(forbidden_prefixes):
            observed.append(event)

    request = make_request()
    evidence = make_evidence()
    sys.addaudithook(hook)
    report = evaluate_verification(request, evidence)
    evaluate_verification(request, {"commit_identity": {"bogus": 1}})
    digest_of(report)
    assert observed == []
    assert report.verdict is Verdict.PASS


def test_module_contains_no_unsafe_call_sites():
    source = Path(pcv.__file__).read_text(encoding="utf-8")
    code_lines = [
        line
        for line in source.splitlines()
        if line.strip() and not line.strip().startswith(("#", '"', "'", "*"))
    ]
    code = "\n".join(code_lines)
    forbidden = [
        r"\bsubprocess\b",
        r"\bos\.system\b",
        r"shell\s*=\s*True",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"__import__",
        r"\bpickle\b",
        r"\bsocket\b",
        r"\bsmtplib\b",
        r"\burllib\b",
        r"\brequests\b",
        r"\bhttpx\b",
        r"\bPopen\b",
        r"\bos\.popen\b",
        r"\bos\.exec",
        r"\bos\.spawn",
        r"\bctypes\b",
        r"\bpty\b",
        r"\bos\.environ\b",
        r"\bgetenv\b",
        r"\bopen\s*\(",
        r"\bPath\s*\(",
    ]
    for pattern in forbidden:
        assert re.search(pattern, code) is None, f"unsafe construct found: {pattern}"

    imported = {
        line.split()[1].split(".")[0]
        for line in code_lines
        if line.startswith("import ") or line.startswith("from ")
    }
    assert imported <= {"__future__", "hashlib", "json", "re", "dataclasses", "enum", "typing"}
