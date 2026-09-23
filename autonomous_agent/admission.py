from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .production_audit import ProductionAudit
from .readiness import ReadinessReport

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

@dataclass(frozen=True)
class AdmissionRequest:
    """Non-secret evidence binding required before canonical execution."""

    task_id: str
    execution_id: str
    expected_task_digest: str
    expected_authorization_digest: str
    side_effects: bool = False
    explicitly_approved: bool = False

@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason: str
    digest: str

def _validate_request(request: AdmissionRequest) -> str | None:
    if not request.task_id.strip() or len(request.task_id) > 128:
        return "admission task identity is invalid"
    if not request.execution_id.strip() or len(request.execution_id) > 128:
        return "admission execution identity is invalid"
    if not _HEX64.fullmatch(request.expected_task_digest):
        return "admission task digest is invalid"
    if not _HEX64.fullmatch(request.expected_authorization_digest):
        return "admission authorization digest is invalid"
    return None

def _decision_digest(
    request: AdmissionRequest,
    *,
    actual_task_digest: str,
    actual_authorization_digest: str,
    readiness: ReadinessReport,
    production_audit: ProductionAudit,
) -> str:
    payload = {
        "request": {
            "task_id": request.task_id,
            "execution_id": request.execution_id,
            "expected_task_digest": request.expected_task_digest,
            "expected_authorization_digest": request.expected_authorization_digest,
            "side_effects": bool(request.side_effects),
            "explicitly_approved": bool(request.explicitly_approved),
        },
        "actual_task_digest": actual_task_digest,
        "actual_authorization_digest": actual_authorization_digest,
        "readiness": [
            {"name": check.name, "passed": bool(check.passed)}
            for check in readiness.checks
        ],
        "production_audit": [
            {"area": finding.area, "passed": bool(finding.passed)}
            for finding in production_audit.findings
        ],
        "readiness_ready": bool(readiness.ready),
        "production_audit_passed": bool(production_audit.passed),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

def evaluate_admission(
    request: AdmissionRequest,
    *,
    actual_task_digest: str,
    actual_authorization_digest: str,
    readiness: ReadinessReport,
    production_audit: ProductionAudit,
) -> AdmissionDecision:
    """Fail-closed admission gate for the canonical execution boundary."""

    invalid = _validate_request(request)
    digest = _decision_digest(
        request,
        actual_task_digest=actual_task_digest,
        actual_authorization_digest=actual_authorization_digest,
        readiness=readiness,
        production_audit=production_audit,
    )
    if invalid:
        return AdmissionDecision(False, invalid, digest)
    if request.task_id != request.task_id.strip():
        return AdmissionDecision(False, "admission task identity is not normalized", digest)
    if request.execution_id != request.execution_id.strip():
        return AdmissionDecision(False, "admission execution identity is not normalized", digest)
    if actual_task_digest != request.expected_task_digest:
        return AdmissionDecision(False, "admission task digest does not match canonical execution", digest)
    if actual_authorization_digest != request.expected_authorization_digest:
        return AdmissionDecision(False, "admission authorization digest does not match canonical execution", digest)
    if not readiness.ready:
        return AdmissionDecision(False, "readiness gate is not satisfied", digest)
    if not production_audit.passed:
        return AdmissionDecision(False, "production audit gate is not satisfied", digest)
    if request.side_effects and not request.explicitly_approved:
        return AdmissionDecision(False, "side-effecting execution requires explicit approval", digest)
    return AdmissionDecision(True, "canonical admission gates satisfied", digest)

__all__ = ["AdmissionDecision", "AdmissionRequest", "evaluate_admission"]
