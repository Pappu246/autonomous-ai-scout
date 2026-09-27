"""Phase 6 M4 — exact-SHA post-change test attestation and final gate.

Pure validation only. No test execution, workflow dispatch, network, credentials,
filesystem access or persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from .post_change_verification import (
    MAX_NAME_CHARS,
    _NAME_RE,
    _bounded_detail,
    _require_bool,
    _require_sha,
    _require_text,
    VerificationError,
    VerificationRequest,
    Stage,
    StageResult,
    StageState,
    Verdict,
    digest_of,
)
from .post_change_evidence import (
    CommitObservation,
    PullRequestObservation,
    ReadOnlyVerificationState,
    evaluate_read_only_identity,
    evaluate_read_only_pr,
)
from .post_change_snapshot import SnapshotObservation, SnapshotPolicy, evaluate_file_snapshot

MAX_RUN_ID: Final[int] = 10**15
MAX_CHECKS: Final[int] = 256
MAX_NAME_SET: Final[int] = 64
ALLOWED_EVENTS: Final[frozenset[str]] = frozenset({"push", "pull_request"})
DEFAULT_WORKFLOWS: Final[frozenset[str]] = frozenset({"CI"})

class CheckConclusion(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    NEUTRAL = "neutral"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"
    UNKNOWN = "unknown"

@dataclass(frozen=True, slots=True)
class CheckObservation:
    name: str
    repository: str
    head_sha: str
    event: str
    status: str
    conclusion: CheckConclusion
    run_id: int
    completed: bool
    workflow_name: str = "CI"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, "check name", max_chars=MAX_NAME_CHARS, pattern=_NAME_RE))
        object.__setattr__(self, "repository", _require_text(self.repository, "repository", max_chars=200, pattern=None))
        object.__setattr__(self, "head_sha", _require_sha(self.head_sha, "head_sha"))
        object.__setattr__(self, "event", _require_text(self.event, "event", max_chars=64, pattern=_NAME_RE))
        object.__setattr__(self, "status", _require_text(self.status, "status", max_chars=32, pattern=_NAME_RE).lower())
        if not isinstance(self.conclusion, CheckConclusion):
            try:
                object.__setattr__(self, "conclusion", CheckConclusion(self.conclusion))
            except (ValueError, TypeError) as exc:
                raise VerificationError("unknown check conclusion") from exc
        if isinstance(self.run_id, bool) or not isinstance(self.run_id, int) or self.run_id <= 0 or self.run_id > MAX_RUN_ID:
            raise VerificationError("run_id must be a bounded positive integer")
        object.__setattr__(self, "completed", _require_bool(self.completed, "completed"))
        object.__setattr__(self, "workflow_name", _require_text(self.workflow_name, "workflow_name", max_chars=MAX_NAME_CHARS, pattern=_NAME_RE))

@dataclass(frozen=True, slots=True)
class TestAttestation:
    __test__ = False
    repository: str
    commit_sha: str
    checks: tuple[CheckObservation, ...]
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _require_text(self.repository, "repository", max_chars=200, pattern=None))
        object.__setattr__(self, "commit_sha", _require_sha(self.commit_sha, "commit_sha"))
        if not isinstance(self.checks, tuple):
            object.__setattr__(self, "checks", tuple(self.checks))
        if len(self.checks) > MAX_CHECKS:
            raise VerificationError("too many checks")
        if any(not isinstance(c, CheckObservation) for c in self.checks):
            raise VerificationError("checks must be CheckObservation values")
        names=[c.name for c in self.checks]
        if len(names) != len(set(names)):
            raise VerificationError("duplicate check names are contradictory")
        object.__setattr__(self, "complete", _require_bool(self.complete, "complete"))

    def canonical(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "checks": [
                {
                    "name": c.name, "repository": c.repository, "head_sha": c.head_sha,
                    "event": c.event, "status": c.status,
                    "conclusion": c.conclusion.value, "run_id": c.run_id,
                    "completed": c.completed, "workflow_name": c.workflow_name,
                }
                for c in sorted(self.checks, key=lambda x: x.name)
            ],
            "complete": self.complete,
        }

class TestAttestationReader(Protocol):
    __test__ = False
    """Only read-side operation authorized for M4."""

    def read_completed_checks(self, request: VerificationRequest) -> TestAttestation: ...

@dataclass(frozen=True, slots=True)
class TestAttestationPolicy:
    __test__ = False
    allowed_events: frozenset[str] = ALLOWED_EVENTS
    allowed_workflows: frozenset[str] = DEFAULT_WORKFLOWS
    require_all_reported: bool = True

    def __post_init__(self) -> None:
        events=frozenset(self.allowed_events)
        workflows=frozenset(self.allowed_workflows)
        if not events or len(events) > len(ALLOWED_EVENTS) or any(not isinstance(e,str) or not e for e in events):
            raise VerificationError("allowed_events is invalid")
        if not events.issubset(ALLOWED_EVENTS):
            raise VerificationError("unsupported workflow event")
        if not workflows or len(workflows) > MAX_NAME_SET or any(
            not isinstance(w,str) or not w for w in workflows
        ):
            raise VerificationError("allowed_workflows is invalid")
        object.__setattr__(self,"allowed_events",events)
        object.__setattr__(self,"allowed_workflows",workflows)
        object.__setattr__(self,"require_all_reported",_require_bool(self.require_all_reported,"require_all_reported"))

    def canonical(self) -> dict[str, object]:
        return {
            "allowed_events": sorted(self.allowed_events),
            "allowed_workflows": sorted(self.allowed_workflows),
            "require_all_reported": self.require_all_reported,
        }

@dataclass(frozen=True, slots=True)
class TestAttestationResult:
    __test__ = False
    state: ReadOnlyVerificationState
    detail: str
    evaluated: bool = True
    failed_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, ReadOnlyVerificationState):
            raise VerificationError("invalid attestation state")
        object.__setattr__(self,"detail",_bounded_detail(self.detail))
        object.__setattr__(self,"evaluated",_require_bool(self.evaluated,"evaluated"))
        object.__setattr__(self,"failed_checks",tuple(self.failed_checks))

def evaluate_post_change_tests(
    request: VerificationRequest,
    attestation: TestAttestation | None,
    policy: TestAttestationPolicy = TestAttestationPolicy(),
) -> TestAttestationResult:
    if not isinstance(request, VerificationRequest):
        raise VerificationError("request must be VerificationRequest")
    if not isinstance(policy, TestAttestationPolicy):
        raise VerificationError("policy must be TestAttestationPolicy")
    if attestation is None:
        return TestAttestationResult(ReadOnlyVerificationState.MISSING,"test attestation was not collected")
    if not isinstance(attestation, TestAttestation):
        raise VerificationError("attestation must be TestAttestation or None")
    if attestation.repository != request.repository:
        return TestAttestationResult(ReadOnlyVerificationState.FAIL,"test evidence repository differs from request")
    if attestation.commit_sha != request.expected_commit_sha:
        return TestAttestationResult(ReadOnlyVerificationState.STALE,"test evidence targets a different commit")
    if not attestation.complete:
        return TestAttestationResult(ReadOnlyVerificationState.MISSING,"test evidence is incomplete")
    required=set(request.test_policy.required_names)
    allowed_names={check.name for check in request.test_policy.checks}
    reported={check.name:check for check in attestation.checks}
    if policy.require_all_reported:
        unexpected=sorted(set(reported)-allowed_names)
        if unexpected:
            return TestAttestationResult(ReadOnlyVerificationState.MALFORMED,"unexpected check reported: "+",".join(unexpected[:5]))
    missing=sorted(required-set(reported))
    if missing:
        return TestAttestationResult(ReadOnlyVerificationState.MISSING,"required check missing: "+",".join(missing[:5]))
    for check in attestation.checks:
        if check.repository != request.repository:
            return TestAttestationResult(ReadOnlyVerificationState.FAIL,"check belongs to another repository")
        if check.head_sha != request.expected_commit_sha:
            return TestAttestationResult(ReadOnlyVerificationState.STALE,"check targets a different commit")
        if check.event not in policy.allowed_events:
            return TestAttestationResult(ReadOnlyVerificationState.FAIL,"check was produced by a disallowed event")
        if check.workflow_name not in policy.allowed_workflows:
            return TestAttestationResult(ReadOnlyVerificationState.FAIL,"check comes from an unapproved workflow")
        if check.name in required:
            if check.status != "completed" or not check.completed:
                return TestAttestationResult(ReadOnlyVerificationState.BLOCKED,"required check is not completed")
            if check.conclusion is not CheckConclusion.SUCCESS:
                return TestAttestationResult(ReadOnlyVerificationState.FAIL,"required check is not successful: "+check.name,failed_checks=(check.name,))
    return TestAttestationResult(ReadOnlyVerificationState.PASS,"all required checks completed successfully for the exact commit")

@dataclass(frozen=True, slots=True)
class Phase6Evidence:
    commit: CommitObservation | None = None
    pull_request: PullRequestObservation | None = None
    snapshot: SnapshotObservation | None = None
    tests: TestAttestation | None = None

    def canonical(self) -> dict[str, object]:
        return {
            "commit": None if self.commit is None else {
                "repository": self.commit.repository,
                "commit_sha": self.commit.commit_sha,
                "head_branch": self.commit.head_branch,
                "base_branch": self.commit.base_branch,
                "head_present": self.commit.head_present,
            },
            "pull_request": None if self.pull_request is None else {
                "repository": self.pull_request.repository,
                "number": self.pull_request.number,
                "state": self.pull_request.state,
                "draft": self.pull_request.draft,
                "merged": self.pull_request.merged,
                "head_repository": self.pull_request.head_repository,
                "head_branch": self.pull_request.head_branch,
                "base_branch": self.pull_request.base_branch,
                "head_commit_sha": self.pull_request.head_commit_sha,
                "head_present": self.pull_request.head_present,
            },
            "snapshot": None if self.snapshot is None else self.snapshot.canonical(),
            "tests": None if self.tests is None else self.tests.canonical(),
        }

@dataclass(frozen=True, slots=True)
class Phase6VerificationReport:
    verdict: Verdict
    stages: tuple[StageResult, ...]
    request_digest: str
    evidence_digest: str
    policy_digest: str
    summary: str

    def __post_init__(self) -> None:
        order=(Stage.COMMIT_IDENTITY,Stage.PULL_REQUEST_STATE,Stage.FILE_SNAPSHOT,Stage.POST_CHANGE_TESTS,Stage.FINAL_RESULT)
        if tuple(stage.stage for stage in self.stages) != order:
            raise VerificationError("invalid Phase 6 stage order")
        if not isinstance(self.verdict,Verdict):
            raise VerificationError("verdict must be Verdict")
        object.__setattr__(self,"summary",_bounded_detail(self.summary))

    @property
    def passed(self)->bool:
        return self.verdict is Verdict.PASS

    def stage(self, stage: Stage)->StageResult:
        for item in self.stages:
            if item.stage is stage:
                return item
        raise VerificationError("unknown stage")

    def canonical(self)->dict[str,object]:
        return {
            "verdict":self.verdict.value,
            "stages":[s.canonical() for s in self.stages],
            "request_digest":self.request_digest,
            "evidence_digest":self.evidence_digest,
            "policy_digest":self.policy_digest,
            "summary":self.summary,
        }

def _map_state(state: ReadOnlyVerificationState)->StageState:
    return StageState(state.value)

def _blocked(stage: Stage, detail: str)->StageResult:
    return StageResult(stage,StageState.BLOCKED,detail,evaluated=False)

def _safe(fn,*args):
    try:
        return fn(*args)
    except VerificationError as exc:
        return type("MalformedResult",(),{
            "state":ReadOnlyVerificationState.MALFORMED,
            "detail":str(exc),
            "evaluated":True,
        })()

def verify_phase6(
    request: VerificationRequest,
    evidence: Phase6Evidence,
    snapshot_policy: SnapshotPolicy = SnapshotPolicy(),
    test_policy: TestAttestationPolicy = TestAttestationPolicy(),
)->Phase6VerificationReport:
    if not isinstance(request,VerificationRequest):
        raise VerificationError("request must be VerificationRequest")
    if not isinstance(evidence,Phase6Evidence):
        raise VerificationError("evidence must be Phase6Evidence")
    identity=_safe(evaluate_read_only_identity,request,evidence.commit)
    stages=[StageResult(Stage.COMMIT_IDENTITY,_map_state(identity.state),identity.detail,identity.evaluated)]
    if identity.state is not ReadOnlyVerificationState.PASS:
        stages += [_blocked(s,"blocked by commit identity failure") for s in (
            Stage.PULL_REQUEST_STATE,Stage.FILE_SNAPSHOT,Stage.POST_CHANGE_TESTS)]
    else:
        pr=_safe(evaluate_read_only_pr,request,evidence.pull_request,evidence.commit)
        stages.append(StageResult(Stage.PULL_REQUEST_STATE,_map_state(pr.state),pr.detail,pr.evaluated))
        if pr.state is not ReadOnlyVerificationState.PASS:
            stages += [_blocked(s,"blocked by pull-request stage failure") for s in (
                Stage.FILE_SNAPSHOT,Stage.POST_CHANGE_TESTS)]
        else:
            snapshot=_safe(evaluate_file_snapshot,request,evidence.snapshot,snapshot_policy)
            stages.append(StageResult(Stage.FILE_SNAPSHOT,_map_state(snapshot.state),snapshot.detail,snapshot.evaluated))
            if snapshot.state is not ReadOnlyVerificationState.PASS:
                stages.append(_blocked(Stage.POST_CHANGE_TESTS,"blocked by file snapshot stage failure"))
            else:
                tests=_safe(evaluate_post_change_tests,request,evidence.tests,test_policy)
                stages.append(StageResult(Stage.POST_CHANGE_TESTS,_map_state(tests.state),tests.detail,tests.evaluated))
    first=next((s for s in stages if s.state is not StageState.PASS),None)
    if first is None:
        verdict=Verdict.PASS
        final=StageResult(Stage.FINAL_RESULT,StageState.PASS,"all required verification stages passed")
        summary="post-change verification passed"
    elif first.state is StageState.FAIL:
        verdict=Verdict.FAIL
        final=StageResult(Stage.FINAL_RESULT,StageState.FAIL,f"{first.stage.value} failed: {first.detail}")
        summary="post-change verification failed; merge/deploy remains blocked"
    else:
        verdict=Verdict.BLOCKED
        final=StageResult(Stage.FINAL_RESULT,StageState.BLOCKED,f"{first.stage.value} blocked: {first.detail}")
        summary="post-change verification blocked; merge/deploy remains blocked"
    stages.append(final)
    evidence_digest=digest_of(evidence.canonical())
    policy_digest=digest_of({
        "snapshot_policy":{
            "allow_binary":snapshot_policy.allow_binary,
            "allow_symlink":snapshot_policy.allow_symlink,
            "allow_submodule":snapshot_policy.allow_submodule,
            "allow_rename":snapshot_policy.allow_rename,
            "max_file_bytes":snapshot_policy.max_file_bytes,
        },
        "test_policy":test_policy.canonical(),
    })
    return Phase6VerificationReport(
        verdict,tuple(stages),digest_of(request),evidence_digest,policy_digest,summary
    )
