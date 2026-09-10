from __future__ import annotations

import hashlib
from dataclasses import dataclass


MAX_PATCH_BYTES = 512_000
MAX_FILES = 20
_FORBIDDEN_PATH_PARTS = {
    ".git",
    ".github/workflows",
    ".env",
    "state/secrets",
}


@dataclass(frozen=True)
class PatchReview:
    allowed: bool
    reason: str
    patch_digest: str
    files: tuple[str, ...]
    additions: int
    deletions: int


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def extract_changed_files(unified_diff: str) -> tuple[str, ...]:
    files: list[str] = []
    for line in unified_diff.splitlines():
        if line.startswith("+++ b/"):
            path = _normalize_path(line[6:])
            if path == "/dev/null":
                continue
            if path not in files:
                files.append(path)
    return tuple(files)


def review_patch(unified_diff: str) -> PatchReview:
    """Validate a reviewable patch artifact without applying or executing it."""
    raw = unified_diff.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_PATCH_BYTES:
        return PatchReview(False, "patch exceeds maximum size", digest, (), 0, 0)

    files = extract_changed_files(unified_diff)
    if len(files) > MAX_FILES:
        return PatchReview(False, "patch touches too many files", digest, files, 0, 0)
    if any(any(part in path for part in _FORBIDDEN_PATH_PARTS) for path in files):
        return PatchReview(False, "patch touches a forbidden path", digest, files, 0, 0)
    if "\x00" in unified_diff:
        return PatchReview(False, "patch contains NUL bytes", digest, files, 0, 0)
    if not unified_diff.strip():
        return PatchReview(False, "patch is empty", digest, files, 0, 0)

    additions = sum(1 for line in unified_diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in unified_diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return PatchReview(True, "patch is reviewable and has not been applied", digest, files, additions, deletions)
