"""Phase 6 M1 — pure decision core for the post-change verification gate.

This module is intentionally a *pure* contract and decision layer:

* no filesystem access
* no network access
* no environment / secret reads
* no subprocess or shell execution
* no GitHub client or transport
* no callbacks, providers, or execution hooks
* no persistence

It only defines immutable models, validation/normalisation, deterministic
stage ordering, bounded and redacted result detail, canonical representation
and digests, and fail-closed verdict logic.

Evidence *acquisition* is explicitly out of scope for this milestone: callers
supply already-collected evidence structures and receive a deterministic
report.  The authoritative gate order is::

    commit identity
      -> PR reviewability
        -> file snapshot
          -> post-change tests
            -> final result
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping, Sequence

__all__ = [
    "MAX_DETAIL_CHARS",
    "MAX_INPUT_DEPTH",
    "MAX_MANIFEST_ENTRIES",
    "MAX_REQUIRED_CHECKS",
    "STAGE_ORDER",
    "CheckOutcome",
    "CommitIdentityEvidence",
    "FileManifestEntry",
    "FileSnapshotEntry",
    "FileSnapshotEvidence",
    "PostChangeTestEvidence",
    "PullRequestEvidence",
    "RequiredCheck",
    "Stage",
    "StageResult",
    "StageState",
    "TestPolicy",
    "VerificationEvidence",
    "VerificationError",
    "VerificationReport",
    "VerificationRequest",
    "Verdict",
    "canonical_json",
    "digest_of",
    "evaluate_verification",
    "redact",
]


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------

MAX_REPOSITORY_CHARS: Final[int] = 200
MAX_BRANCH_CHARS: Final[int] = 255
MAX_PATH_CHARS: Final[int] = 400
MAX_NAME_CHARS: Final[int] = 120
MAX_DETAIL_CHARS: Final[int] = 240
MAX_MANIFEST_ENTRIES: Final[int] = 512
MAX_SNAPSHOT_ENTRIES: Final[int] = 1024
MAX_REQUIRED_CHECKS: Final[int] = 64
MAX_CHECK_OUTCOMES: Final[int] = 256
MAX_INPUT_DEPTH: Final[int] = 6
MAX_PR_NUMBER: Final[int] = 10_000_000

_SHA_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{40}\Z")
_CONTENT_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")
_REPOSITORY_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z")
_BRANCH_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_PATH_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_NAME_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._ /-]*\Z")

REVIEWABLE_PR_STATES: Final[frozenset[str]] = frozenset({"open", "draft"})
KNOWN_PR_STATES: Final[frozenset[str]] = frozenset(
    {"open", "draft", "closed", "merged", "locked", "unknown"}
)


class VerificationError(ValueError):
    """Raised for structurally invalid requests or evidence (fail-closed)."""


# --------------------------------------------------------------------------
# Redaction / bounding helpers
# --------------------------------------------------------------------------

_REDACTION: Final[str] = "[REDACTED]"

_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:bearer|basic|token)\s+[A-Za-z0-9._\-/+=]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{12,}"),
    re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b[A-Z0-9_]*(?:secret|token|password|passwd|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|credential)[A-Z0-9_]*\s*[:=]\s*[^\s,;]+"
    ),
)


def redact(text: str) -> str:
    """Remove likely credentials from free text.  Never echoes raw secrets."""
    if not isinstance(text, str):
        raise VerificationError("detail text must be a string")
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(_REDACTION, cleaned)
    return cleaned


def _bounded_detail(text: str) -> str:
    """Redact, strip control characters, and hard-bound a detail string."""
    cleaned = redact(text if isinstance(text, str) else str(text))
    cleaned = "".join(ch if ch.isprintable() else " " for ch in cleaned).strip()
    if len(cleaned) > MAX_DETAIL_CHARS:
        cleaned = cleaned[: MAX_DETAIL_CHARS - 3] + "..."
    return cleaned


# --------------------------------------------------------------------------
# Primitive validators
# --------------------------------------------------------------------------


def _require_bool(value: Any, label: str) -> bool:
    if value is not True and value is not False:
        raise VerificationError(f"{label} must be a real boolean")
    return value


def _require_text(value: Any, label: str, *, max_chars: int, pattern: re.Pattern[str] | None) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise VerificationError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise VerificationError(f"{label} must be non-empty")
    if len(normalized) > max_chars:
        raise VerificationError(f"{label} exceeds {max_chars} characters")
    if pattern is not None and not pattern.match(normalized):
        raise VerificationError(f"{label} is not a valid value")
    return normalized


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise VerificationError(f"{label} must be a string")
    normalized = value.strip().lower()
    if not _SHA_RE.match(normalized):
        raise VerificationError(f"{label} must be exactly 40 lowercase hex characters")
    return normalized


def _require_content_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise VerificationError(f"{label} must be a string")
    normalized = value.strip().lower()
    if not _CONTENT_DIGEST_RE.match(normalized):
        raise VerificationError(f"{label} must be a 64 character sha256 hex digest")
    return normalized


def _require_pr_number(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VerificationError(f"{label} must be an integer (bool rejected)")
    if value <= 0 or value > MAX_PR_NUMBER:
        raise VerificationError(f"{label} must be a positive bounded integer")
    return value


def _require_sequence(value: Any, label: str, *, max_items: int) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise VerificationError(f"{label} must be a sequence")
    items = tuple(value)
    if len(items) > max_items:
        raise VerificationError(f"{label} exceeds {max_items} entries")
    return items


def _require_mapping(value: Any, label: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise VerificationError(f"{label} must be a mapping")
    keys = set()
    for key in value.keys():
        if not isinstance(key, str):
            raise VerificationError(f"{label} keys must be strings")
        keys.add(key)
    unknown = sorted(keys - allowed)
    if unknown:
        raise VerificationError(f"{label} has unknown fields: {', '.join(unknown)}")
    return {key: value[key] for key in sorted(keys)}


def _check_depth(value: Any, label: str, depth: int = 0) -> None:
    """Reject hostile / deeply nested input without unbounded recursion."""
    if depth > MAX_INPUT_DEPTH:
        raise VerificationError(f"{label} nesting exceeds depth {MAX_INPUT_DEPTH}")
    if isinstance(value, Mapping):
        if len(value) > MAX_SNAPSHOT_ENTRIES:
            raise VerificationError(f"{label} mapping is too large")
        for key, item in value.items():
            _check_depth(key, label, depth + 1)
            _check_depth(item, label, depth + 1)
        return
    if isinstance(value, (str, bytes)):
        return
    if isinstance(value, Sequence):
        if len(value) > MAX_SNAPSHOT_ENTRIES:
            raise VerificationError(f"{label} sequence is too large")
        for item in value:
            _check_depth(item, label, depth + 1)
        return
    if isinstance(value, (set, frozenset)):
        raise VerificationError(f"{label} may not contain unordered set values")


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Stage(str, Enum):
    COMMIT_IDENTITY = "commit_identity"
    PULL_REQUEST_STATE = "pull_request_state"
    FILE_SNAPSHOT = "file_snapshot"
    POST_CHANGE_TESTS = "post_change_tests"
    FINAL_RESULT = "final_result"


STAGE_ORDER: Final[tuple[Stage, ...]] = (
    Stage.COMMIT_IDENTITY,
    Stage.PULL_REQUEST_STATE,
    Stage.FILE_SNAPSHOT,
    Stage.POST_CHANGE_TESTS,
    Stage.FINAL_RESULT,
)

EVIDENCE_STAGES: Final[tuple[Stage, ...]] = STAGE_ORDER[:-1]


class StageState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    MISSING = "missing"
    MALFORMED = "malformed"
    STALE = "stale"
    CONTRADICTORY = "contradictory"


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


#: States that are *not* success.  Everything except PASS fails closed.
NON_PASSING_STATES: Final[frozenset[StageState]] = frozenset(
    state for state in StageState if state is not StageState.PASS
)

#: Non-passing states that escalate the final verdict to BLOCKED rather than FAIL.
_BLOCKING_STATES: Final[frozenset[StageState]] = frozenset(
    {
        StageState.BLOCKED,
        StageState.MISSING,
        StageState.MALFORMED,
        StageState.STALE,
        StageState.CONTRADICTORY,
    }
)


# --------------------------------------------------------------------------
# Request model
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileManifestEntry:
    """One expected changed file and its expected content digest."""

    path: str
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "path",
            _require_text(self.path, "manifest path", max_chars=MAX_PATH_CHARS, pattern=_PATH_RE),
        )
        if ".." in self.path.split("/"):
            raise VerificationError("manifest path may not traverse parents")
        object.__setattr__(
            self, "content_sha256", _require_content_digest(self.content_sha256, "manifest digest")
        )

    _FIELDS = frozenset({"path", "content_sha256"})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FileManifestEntry":
        payload = _require_mapping(data, "manifest entry", cls._FIELDS)
        missing = cls._FIELDS - payload.keys()
        if missing:
            raise VerificationError(f"manifest entry missing fields: {', '.join(sorted(missing))}")
        return cls(path=payload["path"], content_sha256=payload["content_sha256"])

    def canonical(self) -> dict[str, Any]:
        return {"path": self.path, "content_sha256": self.content_sha256}


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    """A required post-change test/check the change must satisfy."""

    name: str
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _require_text(self.name, "check name", max_chars=MAX_NAME_CHARS, pattern=_NAME_RE),
        )
        object.__setattr__(self, "required", _require_bool(self.required, "check.required"))

    _FIELDS = frozenset({"name", "required"})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RequiredCheck":
        payload = _require_mapping(data, "required check", cls._FIELDS)
        if "name" not in payload:
            raise VerificationError("required check missing field: name")
        return cls(name=payload["name"], required=payload.get("required", True))

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "required": self.required}


@dataclass(frozen=True, slots=True)
class TestPolicy:
    """Bounded policy describing which checks must pass."""

    __test__ = False  # not a pytest test class

    checks: tuple[RequiredCheck, ...]
    require_all_reported: bool = True
    manifest_unordered: bool = True

    def __post_init__(self) -> None:
        items = _require_sequence(self.checks, "test policy checks", max_items=MAX_REQUIRED_CHECKS)
        parsed: list[RequiredCheck] = []
        for item in items:
            if isinstance(item, RequiredCheck):
                parsed.append(item)
            elif isinstance(item, Mapping):
                parsed.append(RequiredCheck.from_mapping(item))
            else:
                raise VerificationError("test policy checks must be RequiredCheck entries")
        if not parsed:
            raise VerificationError("test policy must declare at least one check")
        names = [check.name for check in parsed]
        if len(set(names)) != len(names):
            raise VerificationError("test policy contains duplicate check names")
        if not any(check.required for check in parsed):
            raise VerificationError("test policy must declare at least one required check")
        object.__setattr__(self, "checks", tuple(parsed))
        object.__setattr__(
            self,
            "require_all_reported",
            _require_bool(self.require_all_reported, "require_all_reported"),
        )
        object.__setattr__(
            self, "manifest_unordered", _require_bool(self.manifest_unordered, "manifest_unordered")
        )

    _FIELDS = frozenset({"checks", "require_all_reported", "manifest_unordered"})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TestPolicy":
        payload = _require_mapping(data, "test policy", cls._FIELDS)
        if "checks" not in payload:
            raise VerificationError("test policy missing field: checks")
        return cls(
            checks=tuple(payload["checks"]),
            require_all_reported=payload.get("require_all_reported", True),
            manifest_unordered=payload.get("manifest_unordered", True),
        )

    @property
    def required_names(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if check.required)

    def canonical(self) -> dict[str, Any]:
        return {
            "checks": [check.canonical() for check in sorted(self.checks, key=lambda c: c.name)],
            "require_all_reported": self.require_all_reported,
            "manifest_unordered": self.manifest_unordered,
        }


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    """Immutable, fully validated verification request."""

    repository: str
    pull_request_number: int
    expected_head_branch: str
    expected_base_branch: str
    expected_commit_sha: str
    expected_files: tuple[FileManifestEntry, ...]
    test_policy: TestPolicy

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "repository",
            _require_text(
                self.repository, "repository", max_chars=MAX_REPOSITORY_CHARS, pattern=_REPOSITORY_RE
            ),
        )
        object.__setattr__(
            self,
            "pull_request_number",
            _require_pr_number(self.pull_request_number, "pull_request_number"),
        )
        head = _require_text(
            self.expected_head_branch,
            "expected_head_branch",
            max_chars=MAX_BRANCH_CHARS,
            pattern=_BRANCH_RE,
        )
        base = _require_text(
            self.expected_base_branch,
            "expected_base_branch",
            max_chars=MAX_BRANCH_CHARS,
            pattern=_BRANCH_RE,
        )
        if head == base:
            raise VerificationError("head and base branches must differ")
        object.__setattr__(self, "expected_head_branch", head)
        object.__setattr__(self, "expected_base_branch", base)
        object.__setattr__(
            self, "expected_commit_sha", _require_sha(self.expected_commit_sha, "expected_commit_sha")
        )

        entries = _require_sequence(
            self.expected_files, "expected_files", max_items=MAX_MANIFEST_ENTRIES
        )
        parsed: list[FileManifestEntry] = []
        for item in entries:
            if isinstance(item, FileManifestEntry):
                parsed.append(item)
            elif isinstance(item, Mapping):
                parsed.append(FileManifestEntry.from_mapping(item))
            else:
                raise VerificationError("expected_files must contain manifest entries")
        if not parsed:
            raise VerificationError("expected_files manifest must be non-empty")
        paths = [entry.path for entry in parsed]
        if len(set(paths)) != len(paths):
            raise VerificationError("expected_files manifest contains duplicate paths")
        object.__setattr__(self, "expected_files", tuple(parsed))

        policy = self.test_policy
        if isinstance(policy, Mapping):
            policy = TestPolicy.from_mapping(policy)
        if not isinstance(policy, TestPolicy):
            raise VerificationError("test_policy must be a TestPolicy")
        object.__setattr__(self, "test_policy", policy)

    _FIELDS = frozenset(
        {
            "repository",
            "pull_request_number",
            "expected_head_branch",
            "expected_base_branch",
            "expected_commit_sha",
            "expected_files",
            "test_policy",
        }
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "VerificationRequest":
        _check_depth(data, "request")
        payload = _require_mapping(data, "request", cls._FIELDS)
        missing = cls._FIELDS - payload.keys()
        if missing:
            raise VerificationError(f"request missing fields: {', '.join(sorted(missing))}")
        return cls(
            repository=payload["repository"],
            pull_request_number=payload["pull_request_number"],
            expected_head_branch=payload["expected_head_branch"],
            expected_base_branch=payload["expected_base_branch"],
            expected_commit_sha=payload["expected_commit_sha"],
            expected_files=tuple(
                _require_sequence(
                    payload["expected_files"], "expected_files", max_items=MAX_MANIFEST_ENTRIES
                )
            ),
            test_policy=payload["test_policy"],
        )

    @property
    def manifest_index(self) -> dict[str, str]:
        return {entry.path: entry.content_sha256 for entry in self.expected_files}

    def canonical(self) -> dict[str, Any]:
        files = [entry.canonical() for entry in self.expected_files]
        if self.test_policy.manifest_unordered:
            files.sort(key=lambda item: item["path"])
        return {
            "repository": self.repository,
            "pull_request_number": self.pull_request_number,
            "expected_head_branch": self.expected_head_branch,
            "expected_base_branch": self.expected_base_branch,
            "expected_commit_sha": self.expected_commit_sha,
            "expected_files": files,
            "test_policy": self.test_policy.canonical(),
        }


# --------------------------------------------------------------------------
# Evidence models
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommitIdentityEvidence:
    """Observed commit identity for the change under verification."""

    observed_commit_sha: str
    observed_head_branch: str
    observed_base_branch: str
    resolved: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_commit_sha", _require_sha(self.observed_commit_sha, "observed_commit_sha")
        )
        object.__setattr__(
            self,
            "observed_head_branch",
            _require_text(
                self.observed_head_branch,
                "observed_head_branch",
                max_chars=MAX_BRANCH_CHARS,
                pattern=_BRANCH_RE,
            ),
        )
        object.__setattr__(
            self,
            "observed_base_branch",
            _require_text(
                self.observed_base_branch,
                "observed_base_branch",
                max_chars=MAX_BRANCH_CHARS,
                pattern=_BRANCH_RE,
            ),
        )
        object.__setattr__(self, "resolved", _require_bool(self.resolved, "resolved"))

    _FIELDS = frozenset(
        {"observed_commit_sha", "observed_head_branch", "observed_base_branch", "resolved"}
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CommitIdentityEvidence":
        payload = _require_mapping(data, "commit identity evidence", cls._FIELDS)
        required = cls._FIELDS - {"resolved"}
        missing = required - payload.keys()
        if missing:
            raise VerificationError(
                f"commit identity evidence missing fields: {', '.join(sorted(missing))}"
            )
        return cls(
            observed_commit_sha=payload["observed_commit_sha"],
            observed_head_branch=payload["observed_head_branch"],
            observed_base_branch=payload["observed_base_branch"],
            resolved=payload.get("resolved", True),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "observed_commit_sha": self.observed_commit_sha,
            "observed_head_branch": self.observed_head_branch,
            "observed_base_branch": self.observed_base_branch,
            "resolved": self.resolved,
        }


@dataclass(frozen=True, slots=True)
class PullRequestEvidence:
    """Observed pull-request reviewability state."""

    number: int
    state: str
    head_branch: str
    base_branch: str
    head_commit_sha: str
    draft: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "number", _require_pr_number(self.number, "pull request number"))
        state = _require_text(self.state, "pull request state", max_chars=32, pattern=_NAME_RE).lower()
        if state not in KNOWN_PR_STATES:
            raise VerificationError("pull request state is not a known state")
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "head_branch",
            _require_text(
                self.head_branch, "pr head_branch", max_chars=MAX_BRANCH_CHARS, pattern=_BRANCH_RE
            ),
        )
        object.__setattr__(
            self,
            "base_branch",
            _require_text(
                self.base_branch, "pr base_branch", max_chars=MAX_BRANCH_CHARS, pattern=_BRANCH_RE
            ),
        )
        object.__setattr__(
            self, "head_commit_sha", _require_sha(self.head_commit_sha, "pr head_commit_sha")
        )
        object.__setattr__(self, "draft", _require_bool(self.draft, "draft"))

    _FIELDS = frozenset(
        {"number", "state", "head_branch", "base_branch", "head_commit_sha", "draft"}
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PullRequestEvidence":
        payload = _require_mapping(data, "pull request evidence", cls._FIELDS)
        required = cls._FIELDS - {"draft"}
        missing = required - payload.keys()
        if missing:
            raise VerificationError(
                f"pull request evidence missing fields: {', '.join(sorted(missing))}"
            )
        return cls(
            number=payload["number"],
            state=payload["state"],
            head_branch=payload["head_branch"],
            base_branch=payload["base_branch"],
            head_commit_sha=payload["head_commit_sha"],
            draft=payload.get("draft", False),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "state": self.state,
            "head_branch": self.head_branch,
            "base_branch": self.base_branch,
            "head_commit_sha": self.head_commit_sha,
            "draft": self.draft,
        }


@dataclass(frozen=True, slots=True)
class FileSnapshotEntry:
    path: str
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "path",
            _require_text(self.path, "snapshot path", max_chars=MAX_PATH_CHARS, pattern=_PATH_RE),
        )
        object.__setattr__(
            self, "content_sha256", _require_content_digest(self.content_sha256, "snapshot digest")
        )

    _FIELDS = frozenset({"path", "content_sha256"})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FileSnapshotEntry":
        payload = _require_mapping(data, "snapshot entry", cls._FIELDS)
        missing = cls._FIELDS - payload.keys()
        if missing:
            raise VerificationError(f"snapshot entry missing fields: {', '.join(sorted(missing))}")
        return cls(path=payload["path"], content_sha256=payload["content_sha256"])

    def canonical(self) -> dict[str, Any]:
        return {"path": self.path, "content_sha256": self.content_sha256}


@dataclass(frozen=True, slots=True)
class FileSnapshotEvidence:
    """Observed file snapshot taken at a specific commit."""

    at_commit_sha: str
    entries: tuple[FileSnapshotEntry, ...]
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_commit_sha", _require_sha(self.at_commit_sha, "at_commit_sha"))
        items = _require_sequence(self.entries, "snapshot entries", max_items=MAX_SNAPSHOT_ENTRIES)
        parsed: list[FileSnapshotEntry] = []
        for item in items:
            if isinstance(item, FileSnapshotEntry):
                parsed.append(item)
            elif isinstance(item, Mapping):
                parsed.append(FileSnapshotEntry.from_mapping(item))
            else:
                raise VerificationError("snapshot entries must be FileSnapshotEntry values")
        object.__setattr__(self, "entries", tuple(parsed))
        object.__setattr__(self, "complete", _require_bool(self.complete, "complete"))

    _FIELDS = frozenset({"at_commit_sha", "entries", "complete"})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FileSnapshotEvidence":
        payload = _require_mapping(data, "file snapshot evidence", cls._FIELDS)
        required = cls._FIELDS - {"complete"}
        missing = required - payload.keys()
        if missing:
            raise VerificationError(
                f"file snapshot evidence missing fields: {', '.join(sorted(missing))}"
            )
        return cls(
            at_commit_sha=payload["at_commit_sha"],
            entries=tuple(
                _require_sequence(payload["entries"], "snapshot entries", max_items=MAX_SNAPSHOT_ENTRIES)
            ),
            complete=payload.get("complete", True),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "at_commit_sha": self.at_commit_sha,
            "entries": [entry.canonical() for entry in sorted(self.entries, key=lambda e: e.path)],
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """Structured outcome of one reported post-change check."""

    name: str
    passed: bool
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _require_text(self.name, "check outcome name", max_chars=MAX_NAME_CHARS, pattern=_NAME_RE),
        )
        object.__setattr__(self, "passed", _require_bool(self.passed, "check outcome passed"))
        detail = self.detail if self.detail is not None else ""
        if not isinstance(detail, str) or isinstance(detail, bool):
            raise VerificationError("check outcome detail must be a string")
        object.__setattr__(self, "detail", _bounded_detail(detail))

    _FIELDS = frozenset({"name", "passed", "detail"})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CheckOutcome":
        payload = _require_mapping(data, "check outcome", cls._FIELDS)
        missing = {"name", "passed"} - payload.keys()
        if missing:
            raise VerificationError(f"check outcome missing fields: {', '.join(sorted(missing))}")
        return cls(name=payload["name"], passed=payload["passed"], detail=payload.get("detail", ""))

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PostChangeTestEvidence:
    """Observed post-change test/check results at a specific commit."""

    at_commit_sha: str
    outcomes: tuple[CheckOutcome, ...]
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_commit_sha", _require_sha(self.at_commit_sha, "at_commit_sha"))
        items = _require_sequence(self.outcomes, "check outcomes", max_items=MAX_CHECK_OUTCOMES)
        parsed: list[CheckOutcome] = []
        for item in items:
            if isinstance(item, CheckOutcome):
                parsed.append(item)
            elif isinstance(item, Mapping):
                parsed.append(CheckOutcome.from_mapping(item))
            else:
                raise VerificationError("check outcomes must be CheckOutcome values")
        object.__setattr__(self, "outcomes", tuple(parsed))
        object.__setattr__(self, "complete", _require_bool(self.complete, "complete"))

    _FIELDS = frozenset({"at_commit_sha", "outcomes", "complete"})

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PostChangeTestEvidence":
        payload = _require_mapping(data, "post change test evidence", cls._FIELDS)
        required = cls._FIELDS - {"complete"}
        missing = required - payload.keys()
        if missing:
            raise VerificationError(
                f"post change test evidence missing fields: {', '.join(sorted(missing))}"
            )
        return cls(
            at_commit_sha=payload["at_commit_sha"],
            outcomes=tuple(
                _require_sequence(payload["outcomes"], "check outcomes", max_items=MAX_CHECK_OUTCOMES)
            ),
            complete=payload.get("complete", True),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "at_commit_sha": self.at_commit_sha,
            "outcomes": [o.canonical() for o in sorted(self.outcomes, key=lambda c: c.name)],
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """Container of already-collected evidence.

    ``None`` means the evidence was never collected and the corresponding stage
    fails closed as MISSING.  There is deliberately **no** verdict/passed field:
    a final verdict can never be supplied by input evidence.
    """

    commit_identity: CommitIdentityEvidence | None = None
    pull_request: PullRequestEvidence | None = None
    file_snapshot: FileSnapshotEvidence | None = None
    post_change_tests: PostChangeTestEvidence | None = None

    def __post_init__(self) -> None:
        pairs = (
            ("commit_identity", CommitIdentityEvidence),
            ("pull_request", PullRequestEvidence),
            ("file_snapshot", FileSnapshotEvidence),
            ("post_change_tests", PostChangeTestEvidence),
        )
        for name, expected in pairs:
            value = getattr(self, name)
            if value is None or isinstance(value, expected):
                continue
            if isinstance(value, Mapping):
                object.__setattr__(self, name, expected.from_mapping(value))
                continue
            raise VerificationError(f"{name} evidence must be {expected.__name__} or None")

    _FIELDS = frozenset(
        {"commit_identity", "pull_request", "file_snapshot", "post_change_tests"}
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "VerificationEvidence":
        _check_depth(data, "evidence")
        payload = _require_mapping(data, "evidence", cls._FIELDS)
        return cls(
            commit_identity=payload.get("commit_identity"),
            pull_request=payload.get("pull_request"),
            file_snapshot=payload.get("file_snapshot"),
            post_change_tests=payload.get("post_change_tests"),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "commit_identity": self.commit_identity.canonical() if self.commit_identity else None,
            "pull_request": self.pull_request.canonical() if self.pull_request else None,
            "file_snapshot": self.file_snapshot.canonical() if self.file_snapshot else None,
            "post_change_tests": (
                self.post_change_tests.canonical() if self.post_change_tests else None
            ),
        }


# --------------------------------------------------------------------------
# Result models
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: Stage
    state: StageState
    detail: str = ""
    evaluated: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.stage, Stage):
            raise VerificationError("stage must be a Stage")
        if not isinstance(self.state, StageState):
            raise VerificationError("state must be a StageState")
        object.__setattr__(self, "detail", _bounded_detail(self.detail))
        object.__setattr__(self, "evaluated", _require_bool(self.evaluated, "evaluated"))

    @property
    def passed(self) -> bool:
        return self.state is StageState.PASS

    def canonical(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "state": self.state.value,
            "detail": self.detail,
            "evaluated": self.evaluated,
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    verdict: Verdict
    stages: tuple[StageResult, ...]
    request_digest: str
    evidence_digest: str
    summary: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, Verdict):
            raise VerificationError("verdict must be a Verdict")
        stages = tuple(self.stages)
        if tuple(stage.stage for stage in stages) != STAGE_ORDER:
            raise VerificationError("stages must follow the authoritative gate order")
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "summary", _bounded_detail(self.summary))

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS

    def stage(self, stage: Stage) -> StageResult:
        for result in self.stages:
            if result.stage is stage:
                return result
        raise VerificationError("unknown stage")

    def canonical(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "stages": [stage.canonical() for stage in self.stages],
            "request_digest": self.request_digest,
            "evidence_digest": self.evidence_digest,
            "summary": self.summary,
        }


# --------------------------------------------------------------------------
# Canonicalisation / digests
# --------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Deterministic canonical JSON for requests, evidence, and reports."""
    payload = value.canonical() if hasattr(value, "canonical") else value
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_of(value: Any) -> str:
    """Bounded deterministic digest of a canonical representation.

    The digest depends only on canonical content, never on object identity,
    and can never contain secrets (it is a fixed-length hex string).
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Stage evaluation (pure)
# --------------------------------------------------------------------------


def _evaluate_commit_identity(
    request: VerificationRequest, evidence: CommitIdentityEvidence | None
) -> StageResult:
    stage = Stage.COMMIT_IDENTITY
    if evidence is None:
        return StageResult(stage, StageState.MISSING, "commit identity evidence was not collected")
    if evidence.resolved is not True:
        return StageResult(stage, StageState.MISSING, "commit identity could not be resolved")
    if evidence.observed_commit_sha != request.expected_commit_sha:
        return StageResult(stage, StageState.FAIL, "observed head commit differs from expected commit")
    if evidence.observed_head_branch != request.expected_head_branch:
        return StageResult(stage, StageState.FAIL, "observed head branch differs from expected branch")
    if evidence.observed_base_branch != request.expected_base_branch:
        return StageResult(stage, StageState.FAIL, "observed base branch differs from expected branch")
    return StageResult(stage, StageState.PASS, "commit identity matches the expected change")


def _evaluate_pull_request(
    request: VerificationRequest,
    evidence: PullRequestEvidence | None,
    commit_evidence: CommitIdentityEvidence,
) -> StageResult:
    stage = Stage.PULL_REQUEST_STATE
    if evidence is None:
        return StageResult(stage, StageState.MISSING, "pull request evidence was not collected")
    if evidence.number != request.pull_request_number:
        return StageResult(stage, StageState.CONTRADICTORY, "pull request number does not match request")
    if evidence.head_commit_sha != commit_evidence.observed_commit_sha:
        return StageResult(
            stage, StageState.CONTRADICTORY, "pull request head disagrees with commit identity evidence"
        )
    if evidence.head_commit_sha != request.expected_commit_sha:
        return StageResult(stage, StageState.STALE, "pull request evidence is stale for expected commit")
    if evidence.head_branch != request.expected_head_branch:
        return StageResult(stage, StageState.FAIL, "pull request head branch differs from expected")
    if evidence.base_branch != request.expected_base_branch:
        return StageResult(stage, StageState.FAIL, "pull request base branch differs from expected")
    if evidence.state == "draft" and evidence.draft is not True:
        return StageResult(stage, StageState.CONTRADICTORY, "draft flag contradicts reported state")
    if evidence.state not in REVIEWABLE_PR_STATES:
        return StageResult(stage, StageState.FAIL, f"pull request is not reviewable: {evidence.state}")
    return StageResult(stage, StageState.PASS, "pull request remains reviewable")


def _evaluate_file_snapshot(
    request: VerificationRequest, evidence: FileSnapshotEvidence | None
) -> StageResult:
    stage = Stage.FILE_SNAPSHOT
    if evidence is None:
        return StageResult(stage, StageState.MISSING, "file snapshot evidence was not collected")
    if evidence.complete is not True:
        return StageResult(stage, StageState.MISSING, "file snapshot evidence is incomplete")
    if evidence.at_commit_sha != request.expected_commit_sha:
        return StageResult(stage, StageState.STALE, "file snapshot was taken at a different commit")
    paths = [entry.path for entry in evidence.entries]
    if len(set(paths)) != len(paths):
        return StageResult(stage, StageState.CONTRADICTORY, "file snapshot reports duplicate paths")
    observed = {entry.path: entry.content_sha256 for entry in evidence.entries}
    expected = request.manifest_index
    missing = sorted(set(expected) - set(observed))
    if missing:
        return StageResult(
            stage, StageState.MISSING, f"snapshot missing expected files: {', '.join(missing[:5])}"
        )
    extra = sorted(set(observed) - set(expected))
    if extra:
        return StageResult(
            stage, StageState.FAIL, f"snapshot contains unexpected files: {', '.join(extra[:5])}"
        )
    mismatched = sorted(path for path in expected if observed[path] != expected[path])
    if mismatched:
        return StageResult(
            stage, StageState.FAIL, f"snapshot content differs for: {', '.join(mismatched[:5])}"
        )
    return StageResult(stage, StageState.PASS, "file snapshot matches the expected manifest")


def _evaluate_post_change_tests(
    request: VerificationRequest, evidence: PostChangeTestEvidence | None
) -> StageResult:
    stage = Stage.POST_CHANGE_TESTS
    if evidence is None:
        return StageResult(stage, StageState.MISSING, "post-change test evidence was not collected")
    if evidence.complete is not True:
        return StageResult(stage, StageState.MISSING, "post-change test evidence is incomplete")
    if evidence.at_commit_sha != request.expected_commit_sha:
        return StageResult(stage, StageState.STALE, "post-change tests ran against a different commit")
    names = [outcome.name for outcome in evidence.outcomes]
    if len(set(names)) != len(names):
        return StageResult(stage, StageState.CONTRADICTORY, "duplicate check outcomes reported")
    reported = {outcome.name: outcome for outcome in evidence.outcomes}
    policy = request.test_policy
    missing = [name for name in policy.required_names if name not in reported]
    if missing:
        return StageResult(
            stage, StageState.MISSING, f"required checks not reported: {', '.join(sorted(missing)[:5])}"
        )
    if policy.require_all_reported:
        unexpected = sorted(set(reported) - {check.name for check in policy.checks})
        if unexpected:
            return StageResult(
                stage, StageState.MALFORMED, f"unexpected checks reported: {', '.join(unexpected[:5])}"
            )
    failed = sorted(name for name in policy.required_names if reported[name].passed is not True)
    if failed:
        return StageResult(stage, StageState.FAIL, f"required checks failed: {', '.join(failed[:5])}")
    return StageResult(stage, StageState.PASS, "all required post-change checks passed")


def _blocked(stage: Stage, reason: str) -> StageResult:
    return StageResult(stage, StageState.BLOCKED, reason, evaluated=False)


def _verdict_for(state: StageState) -> Verdict:
    if state is StageState.PASS:
        return Verdict.PASS
    if state in _BLOCKING_STATES:
        return Verdict.BLOCKED
    return Verdict.FAIL


def evaluate_verification(
    request: VerificationRequest | Mapping[str, Any],
    evidence: VerificationEvidence | Mapping[str, Any] | None,
) -> VerificationReport:
    """Evaluate the post-change verification gate.

    Pure function: it performs no filesystem, network, process, or environment
    access, accepts no callbacks/providers/transports, and returns one
    deterministic report with per-stage results and a single final verdict.
    """
    if isinstance(request, Mapping):
        request = VerificationRequest.from_mapping(request)
    if not isinstance(request, VerificationRequest):
        raise VerificationError("request must be a VerificationRequest")

    stages: list[StageResult] = []

    # Evidence structure validation (fail closed, never raise to the caller).
    evidence_error: str | None = None
    if evidence is None:
        parsed_evidence = VerificationEvidence()
    elif isinstance(evidence, VerificationEvidence):
        parsed_evidence = evidence
    elif isinstance(evidence, Mapping):
        try:
            parsed_evidence = VerificationEvidence.from_mapping(evidence)
        except VerificationError as exc:
            parsed_evidence = VerificationEvidence()
            evidence_error = str(exc)
    else:
        parsed_evidence = VerificationEvidence()
        evidence_error = "evidence must be a VerificationEvidence or mapping"

    if evidence_error is not None:
        reason = f"evidence is malformed: {evidence_error}"
        stages.append(StageResult(Stage.COMMIT_IDENTITY, StageState.MALFORMED, reason))
        for stage in EVIDENCE_STAGES[1:]:
            stages.append(_blocked(stage, "blocked by malformed evidence"))
        final_state = StageState.BLOCKED
        stages.append(
            StageResult(Stage.FINAL_RESULT, final_state, "verification blocked: malformed evidence")
        )
        return VerificationReport(
            verdict=Verdict.BLOCKED,
            stages=tuple(stages),
            request_digest=digest_of(request),
            evidence_digest=digest_of(VerificationEvidence()),
            summary="merge/deploy remains blocked; evidence was malformed",
        )

    identity = _evaluate_commit_identity(request, parsed_evidence.commit_identity)
    stages.append(identity)

    if identity.state is not StageState.PASS:
        # Earlier identity failure prevents any later, higher-risk evidence use.
        for stage in EVIDENCE_STAGES[1:]:
            stages.append(_blocked(stage, "blocked by commit identity failure"))
    else:
        assert parsed_evidence.commit_identity is not None  # narrowed by PASS above
        pull_request = _evaluate_pull_request(
            request, parsed_evidence.pull_request, parsed_evidence.commit_identity
        )
        stages.append(pull_request)
        if pull_request.state is not StageState.PASS:
            stages.append(_blocked(Stage.FILE_SNAPSHOT, "blocked by pull request stage failure"))
            stages.append(_blocked(Stage.POST_CHANGE_TESTS, "blocked by pull request stage failure"))
        else:
            snapshot = _evaluate_file_snapshot(request, parsed_evidence.file_snapshot)
            stages.append(snapshot)
            if snapshot.state is not StageState.PASS:
                stages.append(_blocked(Stage.POST_CHANGE_TESTS, "blocked by file snapshot failure"))
            else:
                stages.append(
                    _evaluate_post_change_tests(request, parsed_evidence.post_change_tests)
                )

    first_failure = next((s for s in stages if s.state is not StageState.PASS), None)
    if first_failure is None:
        verdict = Verdict.PASS
        final_state = StageState.PASS
        summary = "post-change verification passed"
        final_detail = "all required stages passed"
    else:
        verdict = _verdict_for(first_failure.state)
        final_state = StageState.FAIL if verdict is Verdict.FAIL else StageState.BLOCKED
        summary = "merge/deploy remains blocked; post-change verification did not pass"
        final_detail = (
            f"{first_failure.stage.value} reported {first_failure.state.value}: {first_failure.detail}"
        )

    stages.append(StageResult(Stage.FINAL_RESULT, final_state, final_detail))

    return VerificationReport(
        verdict=verdict,
        stages=tuple(stages),
        request_digest=digest_of(request),
        evidence_digest=digest_of(parsed_evidence),
        summary=summary,
    )
