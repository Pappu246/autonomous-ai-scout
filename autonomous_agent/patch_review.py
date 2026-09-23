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

_HUNK_FULL_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

def _parse_diff_hunks(unified_diff: str) -> dict[str, list[tuple[int, int, int, int, list[str]]]] | None:
    current_path: str | None = None
    current: tuple[int, int, int, int, list[str]] | None = None
    parsed: dict[str, list[tuple[int, int, int, int, list[str]]]] = {}

    def flush() -> bool:
        nonlocal current
        if current_path is None or current is None:
            return True
        old_start, old_count, new_start, new_count, lines = current
        old_seen = sum(1 for line in lines if line[0] in {" ", "-"})
        new_seen = sum(1 for line in lines if line[0] in {" ", "+"})
        if old_seen != old_count or new_seen != new_count:
            return False
        parsed.setdefault(current_path, []).append(current)
        current = None
        return True

    for line in unified_diff.splitlines():
        if line.startswith("+++ b/"):
            if not flush():
                return None
            current_path = _normalize_path(line[6:])
            continue
        if line.startswith("@@ "):
            if not flush():
                return None
            match = _HUNK_FULL_RE.match(line)
            if not match:
                return None
            current = (
                int(match.group(1)),
                int(match.group(2) or "1"),
                int(match.group(3)),
                int(match.group(4) or "1"),
                [],
            )
            continue
        if current is not None:
            if not line:
                current[4].append(" ")
            elif line[0] in {" ", "+", "-"}:
                current[4].append(line)
            elif line.startswith("\ No newline"):
                return None
            else:
                # Diff metadata outside a hunk is allowed; inside a hunk it is not.
                return None

    if not flush():
        return None
    return parsed


def validate_patch_applies_to_base(
    unified_diff: str,
    base_files: Mapping[str, str],
    result_files: Mapping[str, str],
) -> bool:
    """Apply supported hunks to trusted base text and compare exact resulting content."""
    parsed = _parse_diff_hunks(unified_diff)
    if not parsed or set(parsed) != set(result_files):
        return False
    if set(parsed) != set(base_files):
        return False

    for path, hunks in parsed.items():
        base = base_files.get(path)
        result = result_files.get(path)
        if not isinstance(base, str) or not isinstance(result, str):
            return False
        base_lines = base.splitlines()
        result_lines = result.splitlines()
        cursor = 0
        rebuilt: list[str] = []
        trailing_newline = base.endswith("\n") or base.endswith("\r\n")
        for old_start, old_count, new_start, new_count, lines in sorted(hunks, key=lambda item: (item[0], item[2])):
            del new_start, new_count
            index = old_start - 1
            if index < cursor or index > len(base_lines):
                return False
            rebuilt.extend(base_lines[cursor:index])
            old_segment = [line[1:] for line in lines if line[0] in {" ", "-"}]
            new_segment = [line[1:] for line in lines if line[0] in {" ", "+"}]
            if old_segment != base_lines[index:index + old_count]:
                return False
            rebuilt.extend(new_segment)
            cursor = index + old_count
        rebuilt.extend(base_lines[cursor:])
        if rebuilt != result_lines:
            return False
        result_trailing_newline = result.endswith("\n") or result.endswith("\r\n")
        if trailing_newline != result_trailing_newline:
            return False
    return True


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
    if any(line.startswith("\\ No newline at end of file") for line in diff_lines):
        return PatchReview(False, "patches without a trailing newline are unsupported", digest, files, 0, 0)
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
