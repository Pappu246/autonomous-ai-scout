from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from .continuous_improvement import ImprovementProposal
from .patch_review import PatchReview, review_patch

_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|api\s+key|access[_-]?token|access\s+token|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----.*?-----END [A-Z0-9 ]+PRIVATE KEY-----",
    re.S,
)


class ImprovementStatus(str, Enum):
    REJECTED = "rejected"
    VALIDATION_FAILED = "validation_failed"
    READY_FOR_APPROVAL = "ready_for_approval"


@dataclass(frozen=True)
class PatchCandidate:
    """An un-applied patch plus the exact resulting file contents."""
    unified_diff: str
    file_contents: Mapping[str, str]
    summary: str
    test_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    detail: str


@dataclass(frozen=True)
class ImprovementAttempt:
    revision: int
    patch_digest: str
    files: tuple[str, ...]
    validation: ValidationResult


@dataclass(frozen=True)
class ImprovementRun:
    status: ImprovementStatus
    proposal_fingerprint: str
    attempts: tuple[ImprovementAttempt, ...]
    candidate: PatchCandidate | None
    review: PatchReview | None
    reason: str
    approval_required: bool


class PatchGenerator(Protocol):
    def generate(
        self,
        proposal: ImprovementProposal,
        *,
        feedback: str = "",
        previous: PatchCandidate | None = None,
    ) -> PatchCandidate | None: ...


class PatchValidator(Protocol):
    def validate(
        self,
        proposal: ImprovementProposal,
        candidate: PatchCandidate,
        review: PatchReview,
    ) -> ValidationResult: ...


def _safe(value: object, limit: int = 1000) -> str:
    text = _PRIVATE_KEY.sub("[REDACTED]", str(value))
    return _SECRET.sub("[REDACTED]", text)[:limit]


def _normalize_manifest(file_contents: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            path.strip().replace("\\", "/").removeprefix("./")
            for path in file_contents
        )
    )


_HUNK_RE = re.compile(r"^@@ -\\d+(?:,\\d+)? \\+(\\d+)(?:,(\\d+))? @@")


def _diff_matches_candidate(unified_diff: str, file_contents: Mapping[str, str]) -> bool:
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
            current_path = line[6:].strip().replace("\\\\", "/").removeprefix("./")
            continue
        if line.startswith("@@ "):
            flush()
            match = _HUNK_RE.match(line)
            if not match:
                return False
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            current_hunk = (start, count, [])
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
        candidate_lines = content.splitlines()
        segment = candidate_lines[start - 1 : start - 1 + count]
        if tuple(segment) != new_lines:
            return False
    return True


def _contains_sensitive_candidate(candidate: PatchCandidate) -> bool:
    for content in candidate.file_contents.values():
        if _PRIVATE_KEY.search(content) or _SECRET.search(content):
            return True
    return False


def _review_candidate(candidate: PatchCandidate) -> tuple[PatchReview | None, str | None]:
    if not candidate.summary.strip():
        return None, "patch summary is required"
    if len(candidate.test_commands) > 12:
        return None, "too many test commands"
    if any(not command.strip() for command in candidate.test_commands):
        return None, "test commands must be non-empty"
    review = review_patch(candidate.unified_diff)
    if not review.allowed:
        return review, review.reason
    if _contains_sensitive_candidate(candidate):
        return review, "candidate file contents contain sensitive material"
    if not _diff_matches_candidate(candidate.unified_diff, candidate.file_contents):
        return review, "candidate file contents do not match the reviewed diff hunks"
    manifest = _normalize_manifest(candidate.file_contents)
    if manifest != review.files:
        return review, "file manifest does not exactly match the reviewed patch"
    if any(not isinstance(content, str) for content in candidate.file_contents.values()):
        return review, "changed file contents must be text"
    total_bytes = 0
    for content in candidate.file_contents.values():
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            return review, "changed file exceeds maximum size"
        total_bytes += size
    if total_bytes > MAX_TOTAL_FILE_BYTES:
        return review, "changed file contents exceed total size budget"
    if any("\x00" in content for content in candidate.file_contents.values()):
        return review, "changed file contains NUL bytes"
    return review, None


MAX_FILE_BYTES = 200_000
MAX_TOTAL_FILE_BYTES = 1_000_000


class SelfImprovementLoop:
    """Bounded proposal -> patch -> validation -> revision loop.

    The loop may generate and validate multiple candidate revisions, but it never
    writes source files, creates branches, opens PRs, merges, or deploys.
    """

    def __init__(self, *, max_revisions: int = 2):
        if not 0 <= int(max_revisions) <= 3:
            raise ValueError("max_revisions must be between 0 and 3")
        self.max_revisions = int(max_revisions)

    def run(
        self,
        proposal: ImprovementProposal,
        *,
        generator: PatchGenerator,
        validator: PatchValidator,
    ) -> ImprovementRun:
        attempts: list[ImprovementAttempt] = []
        feedback = ""
        previous: PatchCandidate | None = None

        for revision in range(self.max_revisions + 1):
            candidate = generator.generate(proposal, feedback=feedback, previous=previous)
            if candidate is None:
                return ImprovementRun(
                    ImprovementStatus.VALIDATION_FAILED if attempts else ImprovementStatus.REJECTED,
                    proposal.fingerprint,
                    tuple(attempts),
                    None,
                    None,
                    "patch generator returned no candidate",
                    True,
                )

            review, review_error = _review_candidate(candidate)
            if review_error is not None or review is None:
                detail = _safe(review_error or "candidate review failed")
                validation = ValidationResult(False, detail)
                attempts.append(
                    ImprovementAttempt(revision, review.patch_digest if review else "", review.files if review else (), validation)
                )
                feedback = detail
                previous = candidate
                continue

            try:
                validation = validator.validate(proposal, candidate, review)
            except Exception as exc:
                validation = ValidationResult(False, f"validator failed: {type(exc).__name__}")
            validation = ValidationResult(bool(validation.passed), _safe(validation.detail))
            attempts.append(ImprovementAttempt(revision, review.patch_digest, review.files, validation))

            if validation.passed:
                return ImprovementRun(
                    ImprovementStatus.READY_FOR_APPROVAL,
                    proposal.fingerprint,
                    tuple(attempts),
                    candidate,
                    review,
                    "candidate passed patch review and injected validation; human approval is still required",
                    True,
                )

            feedback = validation.detail
            previous = candidate

        last = attempts[-1].validation.detail if attempts else "no candidate was generated"
        return ImprovementRun(
            ImprovementStatus.VALIDATION_FAILED,
            proposal.fingerprint,
            tuple(attempts),
            previous,
            review_patch(previous.unified_diff) if previous is not None else None,
            f"revision budget exhausted: {last}",
            True,
        )


def approval_payload(run: ImprovementRun) -> dict[str, object]:
    """Create a bounded, secret-redacted approval summary without source mutation."""
    return {
        "status": run.status.value,
        "proposal_fingerprint": run.proposal_fingerprint,
        "approval_required": run.approval_required,
        "reason": _safe(run.reason),
        "attempts": tuple(
            {
                "revision": attempt.revision,
                "patch_digest": attempt.patch_digest,
                "files": attempt.files,
                "validation_passed": attempt.validation.passed,
                "validation_detail": _safe(attempt.validation.detail),
            }
            for attempt in run.attempts
        ),
    }
