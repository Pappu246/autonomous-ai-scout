from __future__ import annotations

import hashlib
import re
from typing import Mapping
from dataclasses import dataclass


MAX_PATCH_BYTES = 512_000
MAX_FILES = 20


@dataclass(frozen=True)
class PatchReview:
    allowed: bool
    reason: str
    patch_digest: str
    files: tuple[str, ...]
    additions: int
    deletions: int


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./")


def _is_forbidden_path(path: str) -> bool:
    normalized = _normalize_path(path)
    drive_like = len(normalized) >= 2 and normalized[1] == ":"
    return (
        not normalized
        or normalized.startswith("/")
        or drive_like
        or normalized == ".."
        or normalized.startswith("../")
        or "/../" in normalized
        or normalized.endswith("/..")
        or normalized == ".git"
        or normalized.startswith(".git/")
        or normalized == ".github/workflows"
        or normalized.startswith(".github/workflows/")
        or normalized == ".env"
        or normalized.startswith(".env.")
        or normalized.startswith("state/secrets/")
    )


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



_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

def validate_patch_file_contents(
    unified_diff: str,
    file_contents: Mapping[str, str],
) -> bool:
    """Check that every unified-diff hunk's resulting lines match supplied files."""
    current_path: str | None = None
    current_hunk: tuple[int, int, list[str]] | None = None
    hunks: list[tuple[str, int, int, tuple[str, ...]]] = []

    def flush() -> None:
        nonlocal current_hunk
        if current_path is not None and current_hunk is not None:
            start, count, lines = current_hunk
            hunks.append((current_path, start, count, tuple(lines)))
        current_hunk = None

    for line in unified_diff.splitlines():
        if line.startswith("+++ b/"):
            flush()
            current_path = _normalize_path(line[6:])
            continue
        if line.startswith("@@ "):
            flush()
            match = _HUNK_RE.match(line)
            if not match:
                return False
            current_hunk = (int(match.group(1)), int(match.group(2) or "1"), [])
            continue
        if current_hunk is not None and line and line[0] in {" ", "+"}:
            current_hunk[2].append(line[1:])
        elif current_hunk is not None and line.startswith("\\ No newline"):
            continue

    flush()
    if not hunks or set(path for path, *_ in hunks) != set(file_contents):
        return False
    for path, start, count, new_lines in hunks:
        content = file_contents.get(path)
        if content is None:
            return False
        segment = content.splitlines()[start - 1 : start - 1 + count]
        if tuple(segment) != new_lines:
            return False
    return True


def review_patch(unified_diff: str) -> PatchReview:
    """Validate a reviewable patch artifact without applying or executing it."""
    raw = unified_diff.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_PATCH_BYTES:
        return PatchReview(False, "patch exceeds maximum size", digest, (), 0, 0)

    if not unified_diff.strip():
        return PatchReview(False, "patch is empty", digest, (), 0, 0)

    files = extract_changed_files(unified_diff)
    if not files:
        return PatchReview(False, "patch does not contain a reviewable changed-file manifest", digest, files, 0, 0)
    diff_lines = unified_diff.splitlines()
    if any(line.startswith(("deleted file mode", "new file mode", "rename from ", "rename to ", "copy from ", "copy to ", "GIT binary patch")) for line in diff_lines):
        return PatchReview(False, "patch contains an unsupported file operation", digest, files, 0, 0)
    if "--- /dev/null" in unified_diff or "+++ /dev/null" in unified_diff:
        return PatchReview(False, "file creation/deletion diffs are unsupported by the safe mutation boundary", digest, files, 0, 0)
    if len(files) > MAX_FILES:
        return PatchReview(False, "patch touches too many files", digest, files, 0, 0)
    if not any(line.startswith("@@ ") for line in diff_lines):
        return PatchReview(False, "patch does not contain a reviewable diff hunk", digest, files, 0, 0)
    if any(_is_forbidden_path(path) for path in files):
        return PatchReview(False, "patch touches a forbidden path", digest, files, 0, 0)
    if "\x00" in unified_diff:
        return PatchReview(False, "patch contains NUL bytes", digest, files, 0, 0)
    if not unified_diff.strip():
        return PatchReview(False, "patch is empty", digest, files, 0, 0)

    additions = sum(1 for line in unified_diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in unified_diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return PatchReview(True, "patch is reviewable and has not been applied", digest, files, additions, deletions)
