from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .prompt_injection_guard import TrustLevel
from .tool_registry import ApprovalRequirement, AuditRequirement, ReadWriteMode, RiskLevel, ToolSpec


class Consequence(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalMode(str, Enum):
    AUTONOMOUS = "autonomous"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalDecision:
    mode: ApprovalMode
    consequence: Consequence
    reasons: tuple[str, ...]

    @property
    def allowed_without_approval(self) -> bool:
        return self.mode is ApprovalMode.AUTONOMOUS


class ConsequenceAwareApprovalPolicy:
    """Derive approval mode from tool consequence metadata and trust origin."""

    def evaluate(
        self,
        tool: ToolSpec,
        *,
        origin_trust: TrustLevel = TrustLevel.USER,
        explicitly_approved: bool = False,
    ) -> ApprovalDecision:
        reasons: list[str] = []
        if tool.capability in {"destructive", "billing", "payment", "secrets", "deploy", "merge"}:
            reasons.append("high-impact capability")
        if tool.read_write_mode in {ReadWriteMode.CONTROLLED_WRITE, ReadWriteMode.HIGH_RISK_WRITE}:
            reasons.append("write side effect")
        if tool.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            reasons.append(f"risk level={tool.risk_level.value}")
        if tool.approval_requirement in {ApprovalRequirement.EXPLICIT, ApprovalRequirement.HUMAN_REVIEW}:
            reasons.append(f"registered approval={tool.approval_requirement.value}")
        if tool.audit_requirement is AuditRequirement.REQUIRED:
            reasons.append("audit required")
        if origin_trust in {TrustLevel.EXTERNAL, TrustLevel.TOOL_RESULT, TrustLevel.MEMORY}:
            if tool.read_write_mode is not ReadWriteMode.READ_ONLY:
                reasons.append("untrusted origin for side effect")
        if tool.risk_level is RiskLevel.CRITICAL or tool.capability in {"destructive", "billing", "payment", "secrets"}:
            return ApprovalDecision(ApprovalMode.AUTONOMOUS if explicitly_approved else ApprovalMode.REQUIRE_APPROVAL, Consequence.CRITICAL, tuple(reasons))
        if tool.risk_level is RiskLevel.HIGH or tool.read_write_mode in {ReadWriteMode.CONTROLLED_WRITE, ReadWriteMode.HIGH_RISK_WRITE}:
            return ApprovalDecision(ApprovalMode.AUTONOMOUS if explicitly_approved else ApprovalMode.REQUIRE_APPROVAL, Consequence.HIGH, tuple(reasons))
        if tool.risk_level is RiskLevel.MEDIUM:
            if tool.read_write_mode is ReadWriteMode.READ_ONLY and tool.approval_requirement is ApprovalRequirement.NONE and tool.safe_autonomous:
                return ApprovalDecision(ApprovalMode.AUTONOMOUS, Consequence.MEDIUM, tuple(reasons))
            return ApprovalDecision(ApprovalMode.AUTONOMOUS if explicitly_approved else ApprovalMode.REQUIRE_APPROVAL, Consequence.MEDIUM, tuple(reasons))
        if tool.read_write_mode is not ReadWriteMode.READ_ONLY:
            return ApprovalDecision(ApprovalMode.AUTONOMOUS if explicitly_approved else ApprovalMode.REQUIRE_APPROVAL, Consequence.LOW, tuple(reasons))
        if tool.approval_requirement is not ApprovalRequirement.NONE:
            return ApprovalDecision(ApprovalMode.AUTONOMOUS if explicitly_approved else ApprovalMode.REQUIRE_APPROVAL, Consequence.MEDIUM, tuple(reasons))
        if not tool.safe_autonomous and not explicitly_approved:
            reasons.append("tool is not marked safe for autonomous execution")
            return ApprovalDecision(ApprovalMode.REQUIRE_APPROVAL, Consequence.NONE if tool.read_write_mode is ReadWriteMode.READ_ONLY else Consequence.LOW, tuple(reasons))
        return ApprovalDecision(ApprovalMode.AUTONOMOUS, Consequence.NONE if tool.read_write_mode is ReadWriteMode.READ_ONLY else Consequence.LOW, tuple(reasons))


__all__ = ["ApprovalDecision", "ApprovalMode", "Consequence", "ConsequenceAwareApprovalPolicy"]
