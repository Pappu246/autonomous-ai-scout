from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class AuditFinding:
    area: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ProductionAudit:
    passed: bool
    findings: tuple[AuditFinding, ...]
    reason: str


_REQUIRED_AREAS = (
    "concurrency",
    "replay",
    "queue_bounds",
    "prompt_injection",
    "secret_redaction",
    "shell_safety",
    "approval",
    "ci",
    "external_data",
)


def run_production_audit(findings: Iterable[AuditFinding]) -> ProductionAudit:
    observed = {item.area: item for item in findings}
    ordered = tuple(observed[name] for name in _REQUIRED_AREAS if name in observed)
    missing = tuple(name for name in _REQUIRED_AREAS if name not in observed)
    passed = not missing and all(item.passed for item in ordered)
    reason = "all required production safety areas passed" if passed else (
        "production hardening gate failed; missing or failed evidence: "
        + ", ".join(missing or [item.area for item in ordered if not item.passed])
    )
    return ProductionAudit(passed, ordered, reason)


__all__ = ["AuditFinding", "ProductionAudit", "run_production_audit"]
