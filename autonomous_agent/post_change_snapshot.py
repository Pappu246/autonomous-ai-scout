"""Phase 6 M3 — exact changed-file snapshot evidence.

Pure, bounded validation of a complete file snapshot tied to one repository and
one immutable commit. This module never reads files or GitHub itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from .post_change_evidence import (
    MAX_REPOSITORY_CHARS,
    ReadOnlyVerificationState,
    _require_bool,
    _require_repository,
)
from .post_change_verification import (
    MAX_PATH_CHARS,
    _PATH_RE,
    _require_content_digest,
    _require_sha,
    _require_text,
    VerificationError,
    VerificationRequest,
    FileManifestEntry,
)

MAX_SNAPSHOT_FILES: Final[int] = 1024
MAX_FILE_BYTES: Final[int] = 50 * 1024 * 1024
MAX_SNAPSHOT_MARKER_CHARS: Final[int] = 80

class SnapshotFileStatus(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"

class SnapshotFailure(str, Enum):
    MISSING = "missing"
    STALE = "stale"
    MALFORMED = "malformed"
    CONTRADICTORY = "contradictory"
    UNSAFE_PATH = "unsafe_path"
    TRUNCATED = "truncated"
    UNEXPECTED = "unexpected"
    CONTENT_MISMATCH = "content_mismatch"
    DISALLOWED_TYPE = "disallowed_type"
    RENAMED = "renamed"

@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    path: str
    content_sha256: str
    status: SnapshotFileStatus = SnapshotFileStatus.MODIFIED
    size_bytes: int = 0
    is_binary: bool = False
    is_symlink: bool = False
    is_submodule: bool = False
    previous_path: str | None = None

    def canonical(self) -> dict[str, object]:
        return {
            "path": self.path,
            "content_sha256": self.content_sha256,
            "status": self.status.value,
            "size_bytes": self.size_bytes,
            "is_binary": self.is_binary,
            "is_symlink": self.is_symlink,
            "is_submodule": self.is_submodule,
            "previous_path": self.previous_path,
        }

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "path",
            _require_text(self.path, "snapshot path",
                          max_chars=MAX_PATH_CHARS, pattern=_PATH_RE),
        )
        parts = self.path.split("/")
        if any(part in {"..", ""} for part in parts) or self.path.startswith("/"):
            raise VerificationError("snapshot path is unsafe")
        object.__setattr__(
            self, "content_sha256",
            _require_content_digest(self.content_sha256, "snapshot content digest"),
        )
        if not isinstance(self.status, SnapshotFileStatus):
            try:
                object.__setattr__(self, "status", SnapshotFileStatus(self.status))
            except (TypeError, ValueError) as exc:
                raise VerificationError("snapshot status is not supported") from exc
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise VerificationError("size_bytes must be an integer")
        if self.size_bytes < 0 or self.size_bytes > MAX_FILE_BYTES:
            raise VerificationError("size_bytes is outside the supported bound")
        object.__setattr__(self, "is_binary", _require_bool(self.is_binary, "is_binary"))
        object.__setattr__(self, "is_symlink", _require_bool(self.is_symlink, "is_symlink"))
        object.__setattr__(self, "is_submodule", _require_bool(self.is_submodule, "is_submodule"))
        if self.previous_path is not None:
            previous = _require_text(
                self.previous_path, "previous_path",
                max_chars=MAX_PATH_CHARS, pattern=_PATH_RE,
            )
            if any(part in {"..", ""} for part in previous.split("/")) or previous.startswith("/"):
                raise VerificationError("previous_path is unsafe")
            object.__setattr__(self, "previous_path", previous)

@dataclass(frozen=True, slots=True)
class SnapshotObservation:
    repository: str
    commit_sha: str
    entries: tuple[SnapshotEntry, ...]
    complete: bool = True
    truncated: bool = False
    observed_file_count: int | None = None

    def canonical(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "entries": [entry.canonical() for entry in sorted(self.entries, key=lambda item: item.path)],
            "complete": self.complete,
            "truncated": self.truncated,
            "observed_file_count": self.observed_file_count,
        }

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", _require_repository(self.repository))
        object.__setattr__(self, "commit_sha", _require_sha(self.commit_sha, "commit_sha"))
        if not isinstance(self.entries, tuple):
            object.__setattr__(self, "entries", tuple(self.entries))
        if len(self.entries) > MAX_SNAPSHOT_FILES:
            raise VerificationError("snapshot contains too many files")
        parsed = []
        for item in self.entries:
            if not isinstance(item, SnapshotEntry):
                raise VerificationError("snapshot entries must be SnapshotEntry values")
            parsed.append(item)
        paths=[item.path for item in parsed]
        if len(paths) != len(set(paths)):
            raise VerificationError("snapshot contains duplicate paths")
        object.__setattr__(self, "entries", tuple(parsed))
        object.__setattr__(self, "complete", _require_bool(self.complete, "complete"))
        object.__setattr__(self, "truncated", _require_bool(self.truncated, "truncated"))
        if self.observed_file_count is not None:
            if isinstance(self.observed_file_count, bool) or not isinstance(self.observed_file_count, int):
                raise VerificationError("observed_file_count must be an integer")
            if self.observed_file_count < 0 or self.observed_file_count > MAX_SNAPSHOT_FILES:
                raise VerificationError("observed_file_count is out of bounds")
        if self.truncated or not self.complete:
            return

class SnapshotReader(Protocol):
    """Only read-side operation authorized for M3 snapshot evidence."""

    def read_file_snapshot(self, request: VerificationRequest) -> SnapshotObservation: ...

@dataclass(frozen=True, slots=True)
class SnapshotPolicy:
    allow_binary: bool = False
    allow_symlink: bool = False
    allow_submodule: bool = False
    allow_rename: bool = False
    max_file_bytes: int = MAX_FILE_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(self, "allow_binary", _require_bool(self.allow_binary, "allow_binary"))
        object.__setattr__(self, "allow_symlink", _require_bool(self.allow_symlink, "allow_symlink"))
        object.__setattr__(self, "allow_submodule", _require_bool(self.allow_submodule, "allow_submodule"))
        object.__setattr__(self, "allow_rename", _require_bool(self.allow_rename, "allow_rename"))
        if isinstance(self.max_file_bytes, bool) or not isinstance(self.max_file_bytes, int):
            raise VerificationError("max_file_bytes must be an integer")
        if self.max_file_bytes <= 0 or self.max_file_bytes > MAX_FILE_BYTES:
            raise VerificationError("max_file_bytes is out of bounds")

@dataclass(frozen=True, slots=True)
class SnapshotResult:
    state: ReadOnlyVerificationState
    detail: str
    evaluated: bool = True
    failure: SnapshotFailure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ReadOnlyVerificationState):
            raise VerificationError("state must be a ReadOnlyVerificationState")
        if not isinstance(self.detail, str):
            raise VerificationError("detail must be a string")
        object.__setattr__(self, "detail", self.detail[:MAX_SNAPSHOT_MARKER_CHARS * 3])
        object.__setattr__(self, "evaluated", _require_bool(self.evaluated, "evaluated"))
        if self.failure is not None and not isinstance(self.failure, SnapshotFailure):
            raise VerificationError("failure must be SnapshotFailure or None")

def evaluate_file_snapshot(
    request: VerificationRequest,
    observation: SnapshotObservation | None,
    policy: SnapshotPolicy = SnapshotPolicy(),
) -> SnapshotResult:
    if not isinstance(request, VerificationRequest):
        raise VerificationError("request must be VerificationRequest")
    if not isinstance(policy, SnapshotPolicy):
        raise VerificationError("policy must be SnapshotPolicy")
    if observation is None:
        return SnapshotResult(
            ReadOnlyVerificationState.MISSING,
            "file snapshot evidence was not collected",
            failure=SnapshotFailure.MISSING,
        )
    if not isinstance(observation, SnapshotObservation):
        raise VerificationError("observation must be SnapshotObservation or None")
    if observation.repository != request.repository:
        return SnapshotResult(
            ReadOnlyVerificationState.FAIL,
            "snapshot repository differs from expected repository",
            failure=SnapshotFailure.CONTRADICTORY,
        )
    if observation.commit_sha != request.expected_commit_sha:
        return SnapshotResult(
            ReadOnlyVerificationState.STALE,
            "snapshot was taken at a different commit",
            failure=SnapshotFailure.STALE,
        )
    if observation.truncated or not observation.complete:
        return SnapshotResult(
            ReadOnlyVerificationState.MISSING,
            "snapshot is incomplete or truncated",
            failure=SnapshotFailure.TRUNCATED,
        )
    expected = request.manifest_index
    observed = {entry.path: entry for entry in observation.entries}
    if set(observed) != set(expected):
        missing=sorted(set(expected)-set(observed))
        extra=sorted(set(observed)-set(expected))
        parts=[]
        if missing:
            parts.append("missing="+",".join(missing[:5]))
        if extra:
            parts.append("unexpected="+",".join(extra[:5]))
        return SnapshotResult(
            ReadOnlyVerificationState.FAIL if extra else ReadOnlyVerificationState.MISSING,
            "snapshot manifest mismatch: " + " ".join(parts),
            failure=SnapshotFailure.UNEXPECTED if extra else SnapshotFailure.MISSING,
        )
    for entry in observation.entries:
        if entry.path != entry.path.strip():
            return SnapshotResult(ReadOnlyVerificationState.MALFORMED,"snapshot path is not normalized",failure=SnapshotFailure.UNSAFE_PATH)
        if entry.is_symlink and not policy.allow_symlink:
            return SnapshotResult(ReadOnlyVerificationState.FAIL,"symlink is disallowed",failure=SnapshotFailure.DISALLOWED_TYPE)
        if entry.is_submodule and not policy.allow_submodule:
            return SnapshotResult(ReadOnlyVerificationState.FAIL,"submodule is disallowed",failure=SnapshotFailure.DISALLOWED_TYPE)
        if entry.is_binary and not policy.allow_binary:
            return SnapshotResult(ReadOnlyVerificationState.FAIL,"binary file is disallowed by policy",failure=SnapshotFailure.DISALLOWED_TYPE)
        if entry.previous_path is not None and not policy.allow_rename:
            return SnapshotResult(ReadOnlyVerificationState.FAIL,"renamed file is disallowed",failure=SnapshotFailure.RENAMED)
        if entry.size_bytes > policy.max_file_bytes:
            return SnapshotResult(ReadOnlyVerificationState.FAIL,"file exceeds snapshot size policy",failure=SnapshotFailure.MALFORMED)
        if entry.status not in {SnapshotFileStatus.ADDED, SnapshotFileStatus.MODIFIED}:
            return SnapshotResult(ReadOnlyVerificationState.FAIL,"unsupported file status",failure=SnapshotFailure.RENAMED)
        if entry.content_sha256 != expected[entry.path]:
            return SnapshotResult(ReadOnlyVerificationState.FAIL,f"content digest mismatch for {entry.path}",failure=SnapshotFailure.CONTENT_MISMATCH)
    if observation.observed_file_count is not None and observation.observed_file_count != len(expected):
        return SnapshotResult(ReadOnlyVerificationState.CONTRADICTORY,"snapshot file count contradicts manifest",failure=SnapshotFailure.CONTRADICTORY)
    return SnapshotResult(
        ReadOnlyVerificationState.PASS,
        "complete snapshot matches repository, exact commit and expected manifest",
    )
