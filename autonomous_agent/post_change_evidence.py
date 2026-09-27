"""Phase 6 M2 — narrow read-only commit/PR evidence contract.

This module contains only typed evidence models, a read-only provider protocol,
and deterministic validation of commit identity and pull-request reviewability.
It deliberately performs no I/O, transport, credential access, subprocess
execution, persistence, or mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from .post_change_verification import (
    MAX_BRANCH_CHARS,
    MAX_DETAIL_CHARS,
    MAX_NAME_CHARS,
    _BRANCH_RE,
    _NAME_RE,
    _bounded_detail,
    _require_bool,
    _require_pr_number,
    _require_sha,
    _require_text,
    VerificationError,
    VerificationRequest,
)

__all__ = [
    "CommitObservation",
    "CommitIdentityReader",
    "PRReviewability",
    "PullRequestObservation",
    "PullRequestReader",
    "ReadOnlyEvidenceProvider",
    "ReadOnlyVerificationResult",
    "ReadOnlyVerificationState",
    "evaluate_read_only_identity",
    "evaluate_read_only_pr",
]

MAX_REPOSITORY_CHARS: Final[int] = 200
MAX_STATE_CHARS: Final[int] = 32

_REVIEWABLE_STATES: Final[frozenset[str]] = frozenset({"open"})
_KNOWN_STATES: Final[frozenset[str]] = frozenset(
    {"open", "closed", "merged", "locked", "unknown"}
)


def _require_repository(value: object, label: str = "repository") -> str:
    return _require_text(value, label, max_chars=MAX_REPOSITORY_CHARS,
                         pattern=None)


class ReadOnlyVerificationState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    MISSING = "missing"
    MALFORMED = "malformed"
    STALE = "stale"
    CONTRADICTORY = "contradictory"


class PRReviewability(str, Enum):
    REVIEWABLE = "reviewable"
    DRAFT = "draft"
    CLOSED = "closed"
    MERGED = "merged"
    LOCKED = "locked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CommitObservation:
    repository: str
    commit_sha: str
    head_branch: str
    base_branch: str
    head_present: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _require_repository(self.repository))
        object.__setattr__(self, "commit_sha", _require_sha(self.commit_sha, "commit_sha"))
        object.__setattr__(
            self, "head_branch",
            _require_text(self.head_branch, "head_branch",
                          max_chars=MAX_BRANCH_CHARS, pattern=_BRANCH_RE),
        )
        object.__setattr__(
            self, "base_branch",
            _require_text(self.base_branch, "base_branch",
                          max_chars=MAX_BRANCH_CHARS, pattern=_BRANCH_RE),
        )
        object.__setattr__(self, "head_present", _require_bool(self.head_present, "head_present"))


@dataclass(frozen=True, slots=True)
class PullRequestObservation:
    repository: str
    number: int
    state: str
    draft: bool
    merged: bool
    head_repository: str
    head_branch: str
    base_branch: str
    head_commit_sha: str
    head_present: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _require_repository(self.repository))
        object.__setattr__(self, "number", _require_pr_number(self.number, "number"))
        state = _require_text(self.state, "state", max_chars=MAX_STATE_CHARS,
                              pattern=_NAME_RE).lower()
        if state not in _KNOWN_STATES:
            raise VerificationError("pull-request state is not recognized")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "draft", _require_bool(self.draft, "draft"))
        object.__setattr__(self, "merged", _require_bool(self.merged, "merged"))
        object.__setattr__(self, "head_repository",
                            _require_repository(self.head_repository, "head_repository"))
        object.__setattr__(
            self, "head_branch",
            _require_text(self.head_branch, "head_branch",
                          max_chars=MAX_BRANCH_CHARS, pattern=_BRANCH_RE),
        )
        object.__setattr__(
            self, "base_branch",
            _require_text(self.base_branch, "base_branch",
                          max_chars=MAX_BRANCH_CHARS, pattern=_BRANCH_RE),
        )
        object.__setattr__(
            self, "head_commit_sha",
            _require_sha(self.head_commit_sha, "head_commit_sha"),
        )
        object.__setattr__(self, "head_present",
                            _require_bool(self.head_present, "head_present"))

        if self.merged and self.state != "closed":
            raise VerificationError("merged=True contradicts non-closed PR state")
        if self.state == "open" and self.merged:
            raise VerificationError("open PR cannot be merged")
        if self.state == "closed" and self.draft:
            raise VerificationError("closed PR cannot be represented as an active draft")

    @property
    def reviewability(self) -> PRReviewability:
        if self.merged:
            return PRReviewability.MERGED
        if self.state == "merged":
            return PRReviewability.MERGED
        if self.state == "closed":
            return PRReviewability.CLOSED
        if self.state == "locked":
            return PRReviewability.LOCKED
        if self.state == "open" and self.draft:
            return PRReviewability.DRAFT
        if self.state == "open":
            return PRReviewability.REVIEWABLE
        return PRReviewability.UNKNOWN


class CommitIdentityReader(Protocol):
    """The only commit-side observation operation permitted by M2."""

    def read_commit_identity(self, request: VerificationRequest) -> CommitObservation: ...


class PullRequestReader(Protocol):
    """The only PR-side observation operation permitted by M2."""

    def read_pull_request(self, request: VerificationRequest) -> PullRequestObservation: ...


class ReadOnlyEvidenceProvider(CommitIdentityReader, PullRequestReader, Protocol):
    """Combined M2 evidence surface.

    Deliberately absent: request(), send(), post(), patch(), delete(), merge(),
    dispatch(), file writes, credential access and arbitrary callbacks.
    """


@dataclass(frozen=True, slots=True)
class ReadOnlyVerificationResult:
    state: ReadOnlyVerificationState
    detail: str = ""
    evaluated: bool = True
    reviewability: PRReviewability | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ReadOnlyVerificationState):
            raise VerificationError("state must be a ReadOnlyVerificationState")
        object.__setattr__(self, "detail", _bounded_detail(self.detail))
        object.__setattr__(self, "evaluated", _require_bool(self.evaluated, "evaluated"))
        if self.reviewability is not None and not isinstance(
            self.reviewability, PRReviewability
        ):
            raise VerificationError("reviewability must be a PRReviewability or None")


def evaluate_read_only_identity(
    request: VerificationRequest,
    observation: CommitObservation | None,
) -> ReadOnlyVerificationResult:
    if not isinstance(request, VerificationRequest):
        raise VerificationError("request must be VerificationRequest")
    if observation is not None and not isinstance(observation, CommitObservation):
        raise VerificationError("observation must be CommitObservation or None")
    if observation is None:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.MISSING,
            "commit identity evidence was not collected",
        )
    if not observation.head_present:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "expected commit head is missing or deleted",
        )
    if observation.repository != request.repository:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "observed repository differs from expected repository",
        )
    if observation.commit_sha != request.expected_commit_sha:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "observed commit SHA differs from expected commit",
        )
    if observation.head_branch != request.expected_head_branch:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "observed head branch differs from expected branch",
        )
    if observation.base_branch != request.expected_base_branch:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "observed base branch differs from expected branch",
        )
    return ReadOnlyVerificationResult(
        ReadOnlyVerificationState.PASS,
        "exact repository, SHA, head branch and base branch match",
    )


def evaluate_read_only_pr(
    request: VerificationRequest,
    observation: PullRequestObservation | None,
    identity: CommitObservation | None,
) -> ReadOnlyVerificationResult:
    if not isinstance(request, VerificationRequest):
        raise VerificationError("request must be VerificationRequest")
    if identity is not None and not isinstance(identity, CommitObservation):
        raise VerificationError("identity must be CommitObservation or None")
    if observation is not None and not isinstance(observation, PullRequestObservation):
        raise VerificationError("observation must be PullRequestObservation or None")
    if identity is None:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.BLOCKED,
            "pull-request evaluation blocked by missing commit identity",
            evaluated=False,
        )
    identity_result = evaluate_read_only_identity(request, identity)
    if identity_result.state is not ReadOnlyVerificationState.PASS:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.BLOCKED,
            "pull-request evaluation blocked by commit identity failure",
            evaluated=False,
        )
    if observation is None:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.MISSING,
            "pull-request evidence was not collected",
        )
    if observation.repository != request.repository:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request repository differs from expected repository",
        )
    if observation.number != request.pull_request_number:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.CONTRADICTORY,
            "pull-request number differs from expected request",
        )
    if observation.head_repository != request.repository:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request head repository differs from expected repository",
        )
    if observation.head_commit_sha != request.expected_commit_sha:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.STALE,
            "pull-request head SHA differs from expected commit",
        )
    if observation.head_commit_sha != identity.commit_sha:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.CONTRADICTORY,
            "pull-request head SHA contradicts commit identity evidence",
        )
    if observation.head_branch != request.expected_head_branch:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request head branch differs from expected branch",
        )
    if observation.base_branch != request.expected_base_branch:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request base branch differs from expected branch",
        )
    if not observation.head_present:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request head is deleted or unavailable",
        )
    if observation.merged:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request is already merged",
            reviewability=observation.reviewability,
        )
    if observation.state == "closed":
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request is closed",
            reviewability=observation.reviewability,
        )
    if observation.state == "locked":
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request is locked",
            reviewability=observation.reviewability,
        )
    if observation.state == "open" and observation.draft:
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request is open but still a draft",
            reviewability=observation.reviewability,
        )
    if observation.state != "open":
        return ReadOnlyVerificationResult(
            ReadOnlyVerificationState.FAIL,
            "pull-request is not open and reviewable",
            reviewability=observation.reviewability,
        )
    return ReadOnlyVerificationResult(
        ReadOnlyVerificationState.PASS,
        "pull-request is open, non-draft, exact and unmerged",
        reviewability=observation.reviewability,
    )
