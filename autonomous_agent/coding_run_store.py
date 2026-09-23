from __future__ import annotations

"""Durable, fail-closed storage for reviewed coding runs.

The store intentionally contains only reviewable engineering data. Credentials,
approval tokens, environment data, and provider configuration stay outside this
format in their existing stores/environment boundaries.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .action_queue import PendingAction
from .approved_executor import action_fingerprint
from .continuous_improvement import ImpactAnalysis, ImprovementEvidence, ImprovementProposal, ImprovementRisk
from .draft_pr_automation import file_contents_digest, validate_draft_identity
from .patch_review import MAX_FILES, MAX_PATCH_BYTES, PatchReview, review_patch, validate_patch_file_contents
from .self_improvement import (
    MAX_FILE_BYTES,
    MAX_TOTAL_FILE_BYTES,
    ImprovementAttempt,
    ImprovementRun,
    ImprovementStatus,
    PatchCandidate,
    ValidationResult,
)


SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 2_000_000
MAX_RECORDS = 500
MAX_TEXT_BYTES = 16_000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|api\s+key|access[_-]?token|access\s+token|token|password|secret|authorization|credential)\s*[:=]\s*[^\s,;]+"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----.*?-----END [A-Z0-9 ]+PRIVATE KEY-----", re.S)
_COMMON_SECRET = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16})")


class CodingRunStoreError(ValueError):
    """A persisted coding run cannot be trusted."""


class CodingRunNotFound(CodingRunStoreError):
    """No run is associated with a requested identifier."""


class CodingRunState(str, Enum):
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"


_ALLOWED_TRANSITIONS = {
    CodingRunState.READY_FOR_APPROVAL: frozenset({CodingRunState.APPROVED, CodingRunState.REJECTED, CodingRunState.INVALIDATED}),
    CodingRunState.APPROVED: frozenset({CodingRunState.EXECUTING, CodingRunState.REJECTED, CodingRunState.INVALIDATED}),
    CodingRunState.EXECUTING: frozenset({CodingRunState.EXECUTED, CodingRunState.FAILED}),
    CodingRunState.EXECUTED: frozenset(),
    CodingRunState.FAILED: frozenset(),
    CodingRunState.REJECTED: frozenset(),
    CodingRunState.INVALIDATED: frozenset(),
}


@dataclass(frozen=True)
class CodingExecutionMetadata:
    worker_state: str | None = None
    reason: str | None = None
    pull_request: str | None = None
    ci_status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class StoredCodingRun:
    run_id: str
    repository: str
    base_branch: str
    head_branch: str
    expected_head_sha: str
    proposal: ImprovementProposal
    run: ImprovementRun
    action_id: str
    action_digest: str
    patch_digest: str
    file_contents_digest: str
    state: CodingRunState
    created_at: str
    updated_at: str
    execution: CodingExecutionMetadata = CodingExecutionMetadata()


def _now(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _safe_id(value: str, label: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _IDENTIFIER.fullmatch(text):
        raise CodingRunStoreError(f"invalid {label}")
    return text


def _bounded_text(value: object, label: str, *, allow_empty: bool = False, limit: int = MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str):
        raise CodingRunStoreError(f"{label} must be text")
    if not allow_empty and not value.strip():
        raise CodingRunStoreError(f"{label} must not be empty")
    if len(value.encode("utf-8")) > limit:
        raise CodingRunStoreError(f"{label} exceeds the size limit")
    return value


def _digest(value: object, label: str) -> str:
    text = _bounded_text(value, label, limit=128).lower()
    if not _DIGEST.fullmatch(text):
        raise CodingRunStoreError(f"{label} must be a SHA-256 digest")
    return text


def _timestamp(value: object, label: str) -> str:
    text = _bounded_text(value, label, limit=128)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CodingRunStoreError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise CodingRunStoreError(f"{label} must include a timezone")
    return text


def _mapping(value: object, label: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise CodingRunStoreError(f"{label} has an invalid schema")
    return value


def _text_list(value: object, label: str, *, maximum: int, item_limit: int = 4096) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise CodingRunStoreError(f"{label} has an invalid length")
    return tuple(_bounded_text(item, f"{label} item", limit=item_limit) for item in value)


def _contains_sensitive(value: object) -> bool:
    if isinstance(value, str):
        return bool(_SECRET.search(value) or _PRIVATE_KEY.search(value) or _COMMON_SECRET.search(value))
    if isinstance(value, Mapping):
        return any(_contains_sensitive(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_sensitive(item) for item in value)
    return False


def _redact(value: object, limit: int = 1024) -> str:
    text = _PRIVATE_KEY.sub("[REDACTED]", str(value))
    text = _SECRET.sub("[REDACTED]", text)
    text = _COMMON_SECRET.sub("[REDACTED]", text)
    return text[:limit]


def _proposal_payload(proposal: ImprovementProposal) -> dict[str, object]:
    return {
        "project": proposal.project,
        "problem": proposal.problem,
        "evidence": [{"source": item.source, "summary": item.summary, "confidence": item.confidence, "fingerprint": item.fingerprint} for item in proposal.evidence],
        "proposed_solution": proposal.proposed_solution,
        "expected_benefit": proposal.expected_benefit,
        "affected_area": list(proposal.affected_area),
        "confidence": proposal.confidence,
        "risk": int(proposal.risk),
        "effort": proposal.effort,
        "severity": proposal.severity,
        "urgency": proposal.urgency,
        "impact": proposal.impact,
        "regression_risk": proposal.regression_risk,
        "project_importance": proposal.project_importance,
        "security_impact": proposal.security_impact,
        "reliability_impact": proposal.reliability_impact,
        "validation_strategy": list(proposal.validation_strategy),
        "rollback_strategy": proposal.rollback_strategy,
        "approval_requirement": proposal.approval_requirement,
        "fingerprint": proposal.fingerprint,
        "impact_analysis": {
            "project": proposal.impact_analysis.project,
            "affected_components": list(proposal.impact_analysis.affected_components),
            "dependencies": list(proposal.impact_analysis.dependencies),
            "regression_surface": list(proposal.impact_analysis.regression_surface),
            "required_tests": list(proposal.impact_analysis.required_tests),
            "side_effects": list(proposal.impact_analysis.side_effects),
        },
    }


def _proposal_from_payload(value: object) -> ImprovementProposal:
    data = _mapping(value, "proposal", {
        "project", "problem", "evidence", "proposed_solution", "expected_benefit", "affected_area", "confidence", "risk", "effort", "severity", "urgency", "impact", "regression_risk", "project_importance", "security_impact", "reliability_impact", "validation_strategy", "rollback_strategy", "approval_requirement", "fingerprint", "impact_analysis",
    })
    evidence_data = data["evidence"]
    if not isinstance(evidence_data, list) or not 1 <= len(evidence_data) <= 20:
        raise CodingRunStoreError("proposal evidence has an invalid length")
    evidence = []
    for item in evidence_data:
        entry = _mapping(item, "proposal evidence", {"source", "summary", "confidence", "fingerprint"})
        confidence = entry["confidence"]
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise CodingRunStoreError("proposal evidence confidence is invalid")
        evidence.append(ImprovementEvidence(_bounded_text(entry["source"], "evidence source", limit=512), _bounded_text(entry["summary"], "evidence summary", limit=4096), float(confidence), _digest(entry["fingerprint"], "evidence fingerprint")))
    impact_data = _mapping(data["impact_analysis"], "impact analysis", {"project", "affected_components", "dependencies", "regression_surface", "required_tests", "side_effects"})
    confidence = data["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise CodingRunStoreError("proposal confidence is invalid")
    numeric_fields = ("effort", "severity", "urgency", "impact", "regression_risk", "project_importance", "security_impact", "reliability_impact")
    numbers: dict[str, int] = {}
    for field in numeric_fields:
        if not isinstance(data[field], int) or not 0 <= data[field] <= 100:
            raise CodingRunStoreError(f"proposal {field} is invalid")
        numbers[field] = data[field]
    try:
        risk = ImprovementRisk(data["risk"])
    except (TypeError, ValueError) as exc:
        raise CodingRunStoreError("proposal risk is invalid") from exc
    return ImprovementProposal(
        project=_bounded_text(data["project"], "proposal project", limit=512),
        problem=_bounded_text(data["problem"], "proposal problem", limit=4096),
        evidence=tuple(evidence),
        proposed_solution=_bounded_text(data["proposed_solution"], "proposal solution", limit=4096),
        expected_benefit=_bounded_text(data["expected_benefit"], "proposal benefit", limit=4096),
        affected_area=_text_list(data["affected_area"], "affected area", maximum=20),
        confidence=float(confidence),
        risk=risk,
        validation_strategy=_text_list(data["validation_strategy"], "validation strategy", maximum=20),
        rollback_strategy=_bounded_text(data["rollback_strategy"], "rollback strategy", limit=4096),
        approval_requirement=_bounded_text(data["approval_requirement"], "approval requirement", limit=512),
        fingerprint=_digest(data["fingerprint"], "proposal fingerprint"),
        impact_analysis=ImpactAnalysis(
            _bounded_text(impact_data["project"], "impact project", limit=512),
            _text_list(impact_data["affected_components"], "affected components", maximum=20),
            _text_list(impact_data["dependencies"], "dependencies", maximum=40),
            _text_list(impact_data["regression_surface"], "regression surface", maximum=20),
            _text_list(impact_data["required_tests"], "required tests", maximum=20),
            _text_list(impact_data["side_effects"], "side effects", maximum=20),
        ),
        **numbers,
    )


def _run_payload(run: ImprovementRun) -> dict[str, object]:
    candidate = run.candidate
    review = run.review
    return {
        "status": run.status.value,
        "proposal_fingerprint": run.proposal_fingerprint,
        "attempts": [{"revision": attempt.revision, "patch_digest": attempt.patch_digest, "files": list(attempt.files), "validation": {"passed": attempt.validation.passed, "detail": attempt.validation.detail}} for attempt in run.attempts],
        "candidate": None if candidate is None else {"unified_diff": candidate.unified_diff, "file_contents": dict(candidate.file_contents), "summary": candidate.summary, "test_commands": list(candidate.test_commands)},
        "review": None if review is None else {"allowed": review.allowed, "reason": review.reason, "patch_digest": review.patch_digest, "files": list(review.files), "additions": review.additions, "deletions": review.deletions},
        "reason": run.reason,
        "approval_required": run.approval_required,
    }


def _run_from_payload(value: object) -> ImprovementRun:
    data = _mapping(value, "improvement run", {"status", "proposal_fingerprint", "attempts", "candidate", "review", "reason", "approval_required"})
    try:
        status = ImprovementStatus(data["status"])
    except (TypeError, ValueError) as exc:
        raise CodingRunStoreError("improvement run status is invalid") from exc
    attempts_data = data["attempts"]
    if not isinstance(attempts_data, list) or len(attempts_data) > 4:
        raise CodingRunStoreError("improvement attempts have an invalid length")
    attempts = []
    for item in attempts_data:
        entry = _mapping(item, "improvement attempt", {"revision", "patch_digest", "files", "validation"})
        if not isinstance(entry["revision"], int) or not 0 <= entry["revision"] <= 3:
            raise CodingRunStoreError("improvement attempt revision is invalid")
        validation = _mapping(entry["validation"], "attempt validation", {"passed", "detail"})
        if not isinstance(validation["passed"], bool):
            raise CodingRunStoreError("attempt validation result is invalid")
        attempts.append(ImprovementAttempt(entry["revision"], _digest(entry["patch_digest"], "attempt patch digest"), _text_list(entry["files"], "attempt files", maximum=MAX_FILES), ValidationResult(validation["passed"], _bounded_text(validation["detail"], "attempt validation detail", allow_empty=True, limit=4096))))
    candidate_data = data["candidate"]
    candidate: PatchCandidate | None = None
    if candidate_data is not None:
        entry = _mapping(candidate_data, "candidate", {"unified_diff", "file_contents", "summary", "test_commands"})
        diff = _bounded_text(entry["unified_diff"], "candidate patch", limit=MAX_PATCH_BYTES)
        contents = entry["file_contents"]
        if not isinstance(contents, Mapping) or not contents or len(contents) > MAX_FILES:
            raise CodingRunStoreError("candidate file contents have an invalid schema")
        files: dict[str, str] = {}
        total = 0
        for path, content in contents.items():
            name = _bounded_text(path, "candidate file path", limit=512)
            text = _bounded_text(content, "candidate file content", allow_empty=True, limit=MAX_FILE_BYTES)
            if name in files:
                raise CodingRunStoreError("candidate file path is duplicated")
            total += len(text.encode("utf-8"))
            files[name] = text
        if total > MAX_TOTAL_FILE_BYTES:
            raise CodingRunStoreError("candidate file contents exceed the total size limit")
        candidate = PatchCandidate(diff, files, _bounded_text(entry["summary"], "candidate summary", limit=4096), _text_list(entry["test_commands"], "candidate test commands", maximum=12, item_limit=1024))
    review_data = data["review"]
    review: PatchReview | None = None
    if review_data is not None:
        entry = _mapping(review_data, "patch review", {"allowed", "reason", "patch_digest", "files", "additions", "deletions"})
        if not isinstance(entry["allowed"], bool) or not isinstance(entry["additions"], int) or not isinstance(entry["deletions"], int):
            raise CodingRunStoreError("patch review contains invalid values")
        review = PatchReview(entry["allowed"], _bounded_text(entry["reason"], "patch review reason", limit=4096), _digest(entry["patch_digest"], "review patch digest"), _text_list(entry["files"], "review files", maximum=MAX_FILES), entry["additions"], entry["deletions"])
    if not isinstance(data["approval_required"], bool):
        raise CodingRunStoreError("approval requirement is invalid")
    return ImprovementRun(status, _digest(data["proposal_fingerprint"], "run proposal fingerprint"), tuple(attempts), candidate, review, _bounded_text(data["reason"], "run reason", limit=4096), data["approval_required"])


def _execution_payload(execution: CodingExecutionMetadata) -> dict[str, object]:
    return {
        "worker_state": execution.worker_state,
        "reason": execution.reason,
        "pull_request": execution.pull_request,
        "ci_status": execution.ci_status,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
    }


def _optional_text(value: object, label: str, *, limit: int = 4096) -> str | None:
    return None if value is None else _bounded_text(value, label, limit=limit)


def _execution_from_payload(value: object) -> CodingExecutionMetadata:
    data = _mapping(value, "execution metadata", {"worker_state", "reason", "pull_request", "ci_status", "started_at", "finished_at"})
    return CodingExecutionMetadata(
        _optional_text(data["worker_state"], "worker state", limit=256),
        _optional_text(data["reason"], "execution reason"),
        _optional_text(data["pull_request"], "pull request", limit=2048),
        _optional_text(data["ci_status"], "CI status", limit=256),
        None if data["started_at"] is None else _timestamp(data["started_at"], "execution start time"),
        None if data["finished_at"] is None else _timestamp(data["finished_at"], "execution finish time"),
    )


def _payload(record: StoredCodingRun) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": record.run_id,
        "repository": record.repository,
        "base_branch": record.base_branch,
        "head_branch": record.head_branch,
        "expected_head_sha": record.expected_head_sha,
        "proposal": _proposal_payload(record.proposal),
        "improvement_run": _run_payload(record.run),
        "action_id": record.action_id,
        "action_digest": record.action_digest,
        "patch_digest": record.patch_digest,
        "file_contents_digest": record.file_contents_digest,
        "state": record.state.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "execution": _execution_payload(record.execution),
    }


def _validate_record(record: StoredCodingRun) -> None:
    _safe_id(record.run_id, "run id")
    _safe_id(record.action_id, "action id")
    if validate_draft_identity(record.repository, record.base_branch, record.head_branch, record.expected_head_sha):
        raise CodingRunStoreError("stored repository identity is invalid")
    if record.proposal.project.strip() != record.repository.strip() or record.proposal.impact_analysis.project.strip() != record.repository.strip():
        raise CodingRunStoreError("proposal does not match stored repository identity")
    if record.run.status is not ImprovementStatus.READY_FOR_APPROVAL or not record.run.approval_required:
        raise CodingRunStoreError("stored coding run is not approval-ready")
    if record.run.proposal_fingerprint != record.proposal.fingerprint:
        raise CodingRunStoreError("stored run does not match proposal identity")
    candidate, review = record.run.candidate, record.run.review
    if candidate is None or review is None:
        raise CodingRunStoreError("stored coding run has no reviewed candidate")
    actual_review = review_patch(candidate.unified_diff)
    if not actual_review.allowed or actual_review != review:
        raise CodingRunStoreError("stored patch review does not match candidate")
    if not validate_patch_file_contents(candidate.unified_diff, candidate.file_contents):
        raise CodingRunStoreError("stored candidate file contents do not match reviewed diff")
    manifest = tuple(path.strip().replace("\\", "/").removeprefix("./") for path in candidate.file_contents)
    if manifest != review.files:
        raise CodingRunStoreError("stored candidate file manifest does not match review")
    if record.patch_digest != review.patch_digest:
        raise CodingRunStoreError("stored patch digest does not match review")
    if record.file_contents_digest != file_contents_digest(candidate.file_contents):
        raise CodingRunStoreError("stored file digest does not match candidate")
    _digest(record.action_digest, "action digest")
    _timestamp(record.created_at, "creation time")
    _timestamp(record.updated_at, "update time")
    if _contains_sensitive(_payload(record)):
        raise CodingRunStoreError("stored coding run contains sensitive content")


def _from_payload(value: object) -> StoredCodingRun:
    data = _mapping(value, "coding run", {
        "schema_version", "run_id", "repository", "base_branch", "head_branch", "expected_head_sha", "proposal", "improvement_run", "action_id", "action_digest", "patch_digest", "file_contents_digest", "state", "created_at", "updated_at", "execution",
    })
    if data["schema_version"] != SCHEMA_VERSION:
        raise CodingRunStoreError("coding run schema version is unsupported")
    try:
        state = CodingRunState(data["state"])
    except (TypeError, ValueError) as exc:
        raise CodingRunStoreError("coding run state is invalid") from exc
    record = StoredCodingRun(
        _safe_id(data["run_id"], "run id"),
        _bounded_text(data["repository"], "repository", limit=512),
        _bounded_text(data["base_branch"], "base branch", limit=512),
        _bounded_text(data["head_branch"], "head branch", limit=512),
        _bounded_text(data["expected_head_sha"], "expected HEAD SHA", limit=128).lower(),
        _proposal_from_payload(data["proposal"]),
        _run_from_payload(data["improvement_run"]),
        _safe_id(data["action_id"], "action id"),
        _digest(data["action_digest"], "action digest"),
        _digest(data["patch_digest"], "patch digest"),
        _digest(data["file_contents_digest"], "file contents digest"),
        state,
        _timestamp(data["created_at"], "creation time"),
        _timestamp(data["updated_at"], "update time"),
        _execution_from_payload(data["execution"]),
    )
    _validate_record(record)
    return record


class CodingRunStore:
    """A bounded JSON store for only reviewed, approval-gated coding runs."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, run_id: str) -> Path:
        return self.directory / f"{_safe_id(run_id, 'run id')}.json"

    def _execution_marker(self, run_id: str) -> Path:
        return self.directory / f"{_safe_id(run_id, 'run id')}.executing"

    def _encode(self, record: StoredCodingRun) -> bytes:
        _validate_record(record)
        payload = json.dumps(_payload(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"
        if len(payload) > MAX_RECORD_BYTES:
            raise CodingRunStoreError("coding run record exceeds the size limit")
        return payload

    def _write(self, record: StoredCodingRun, *, create_only: bool = False) -> None:
        payload = self._encode(record)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(record.run_id)
        if create_only and path.exists():
            raise CodingRunStoreError("coding run already exists")
        temporary_name = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{record.run_id}.", suffix=".tmp", dir=self.directory)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            if create_only:
                try:
                    os.link(temporary_name, path)
                except FileExistsError as exc:
                    raise CodingRunStoreError("coding run already exists") from exc
                finally:
                    if Path(temporary_name).exists():
                        Path(temporary_name).unlink()
                temporary_name = None
            else:
                os.replace(temporary_name, path)
                temporary_name = None
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            raise CodingRunStoreError("coding run record could not be persisted") from exc
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def create(
        self,
        *,
        run_id: str,
        proposal: ImprovementProposal,
        run: ImprovementRun,
        action: PendingAction,
        repository: str,
        base_branch: str,
        head_branch: str,
        expected_head_sha: str,
        now: datetime | None = None,
    ) -> StoredCodingRun:
        created = _now(now)
        candidate = run.candidate
        review = run.review
        if candidate is None or review is None:
            raise CodingRunStoreError("coding run requires a reviewed patch candidate")
        record = StoredCodingRun(
            _safe_id(run_id, "run id"),
            repository.strip(),
            base_branch.strip(),
            head_branch.strip(),
            expected_head_sha.strip().lower(),
            proposal,
            run,
            _safe_id(action.id, "action id"),
            action_fingerprint(action),
            review.patch_digest,
            file_contents_digest(candidate.file_contents),
            CodingRunState.READY_FOR_APPROVAL,
            created,
            created,
        )
        self._write(record, create_only=True)
        return record

    def load(self, run_id: str) -> StoredCodingRun:
        path = self._path(run_id)
        try:
            if not path.is_file():
                raise CodingRunNotFound("coding run is missing")
            if path.stat().st_size > MAX_RECORD_BYTES:
                raise CodingRunStoreError("coding run record exceeds the size limit")
            raw = path.read_bytes()
            if not raw or len(raw) > MAX_RECORD_BYTES:
                raise CodingRunStoreError("coding run record is invalid")
            value = json.loads(raw.decode("utf-8"))
        except CodingRunStoreError:
            raise
        except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise CodingRunStoreError("coding run record is malformed") from exc
        return _from_payload(value)

    def find_by_action(self, action_id: str) -> StoredCodingRun:
        safe_action_id = _safe_id(action_id, "action id")
        if not self.directory.exists():
            raise CodingRunNotFound("coding run is missing")
        try:
            paths = sorted(self.directory.glob("*.json"))
        except OSError as exc:
            raise CodingRunStoreError("coding run store is unavailable") from exc
        if len(paths) > MAX_RECORDS:
            raise CodingRunStoreError("coding run store exceeds the record limit")
        found: list[StoredCodingRun] = []
        for path in paths:
            record = self.load(path.stem)
            if record.action_id == safe_action_id:
                found.append(record)
        if not found:
            raise CodingRunNotFound("coding run is missing")
        if len(found) != 1:
            raise CodingRunStoreError("action is bound to multiple coding runs")
        return found[0]

    def _transition(self, record: StoredCodingRun, target: CodingRunState, *, execution: CodingExecutionMetadata | None = None, now: datetime | None = None) -> StoredCodingRun:
        if target not in _ALLOWED_TRANSITIONS[record.state]:
            raise CodingRunStoreError(f"coding run state cannot transition from {record.state.value} to {target.value}")
        updated = replace(record, state=target, updated_at=_now(now), execution=execution or record.execution)
        self._write(updated)
        return updated

    def mark_approved(self, run_id: str, action: PendingAction, *, now: datetime | None = None) -> StoredCodingRun:
        record = self.load(run_id)
        if action.status != "approved" or action.id != record.action_id or action_fingerprint(action) != record.action_digest:
            raise CodingRunStoreError("approved action does not match stored coding run")
        return self._transition(record, CodingRunState.APPROVED, now=now)

    def mark_rejected(self, run_id: str, *, now: datetime | None = None) -> StoredCodingRun:
        return self._transition(self.load(run_id), CodingRunState.REJECTED, now=now)

    def invalidate(self, run_id: str, reason: str, *, now: datetime | None = None) -> StoredCodingRun:
        record = self.load(run_id)
        execution = replace(record.execution, reason=_redact(reason), finished_at=_now(now))
        return self._transition(record, CodingRunState.INVALIDATED, execution=execution, now=now)

    def begin_execution(self, run_id: str, *, now: datetime | None = None) -> StoredCodingRun:
        record = self.load(run_id)
        if record.state is not CodingRunState.APPROVED:
            raise CodingRunStoreError("coding run is not approved for execution")
        marker = self._execution_marker(run_id)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with marker.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps({"run_id": record.run_id, "started_at": _now(now)}, sort_keys=True) + "\n")
            try:
                os.chmod(marker, 0o600)
            except OSError:
                pass
        except FileExistsError as exc:
            raise CodingRunStoreError("coding run was already claimed for execution") from exc
        except OSError as exc:
            raise CodingRunStoreError("coding run execution marker could not be persisted") from exc
        return self._transition(record, CodingRunState.EXECUTING, execution=replace(record.execution, started_at=_now(now)), now=now)

    def mark_executed(self, run_id: str, *, worker_state: str, reason: str, pull_request: str | None, ci_status: str | None, now: datetime | None = None) -> StoredCodingRun:
        record = self.load(run_id)
        execution = replace(record.execution, worker_state=_redact(worker_state, 256), reason=_redact(reason), pull_request=None if pull_request is None else _redact(pull_request, 2048), ci_status=None if ci_status is None else _redact(ci_status, 256), finished_at=_now(now))
        return self._transition(record, CodingRunState.EXECUTED, execution=execution, now=now)

    def record_observation(self, run_id: str, *, worker_state: str, reason: str, ci_status: str | None, now: datetime | None = None) -> StoredCodingRun:
        record = self.load(run_id)
        if record.state is not CodingRunState.EXECUTED:
            raise CodingRunStoreError("coding run observation requires an executed run")
        execution = replace(
            record.execution,
            worker_state=_redact(worker_state, 256),
            reason=_redact(reason),
            ci_status=None if ci_status is None else _redact(ci_status, 256),
        )
        updated = replace(record, execution=execution, updated_at=_now(now))
        self._write(updated)
        return updated

    def mark_failed(self, run_id: str, *, worker_state: str, reason: str, now: datetime | None = None) -> StoredCodingRun:
        record = self.load(run_id)
        execution = replace(record.execution, worker_state=_redact(worker_state, 256), reason=_redact(reason), finished_at=_now(now))
        return self._transition(record, CodingRunState.FAILED, execution=execution, now=now)

    @staticmethod
    def summary(record: StoredCodingRun) -> dict[str, object]:
        return {
            "run_id": record.run_id,
            "action_id": record.action_id,
            "state": record.state.value,
            "repository": record.repository,
            "base_branch": record.base_branch,
            "head_branch": record.head_branch,
            "expected_head_sha": record.expected_head_sha,
            "proposal_fingerprint": record.proposal.fingerprint,
            "patch_digest": record.patch_digest,
            "file_contents_digest": record.file_contents_digest,
            "files": list(record.run.review.files if record.run.review else ()),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "execution": _execution_payload(record.execution),
        }
