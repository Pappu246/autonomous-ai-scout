"""The canonical digital agent lifecycle.

    User Goal
      -> Intent Understanding
      -> Task Planning
      -> Capability Selection
      -> Authorization + Risk Evaluation
      -> Execution
      -> Observation
      -> Verification
      -> Recovery / Retry / Resume
      -> Final Result

The runtime owns the lifecycle; it owns no execution power. Every step runs
through a registered capability bound to the pre-existing sandbox, and every
step is re-authorized against the process tool registry immediately before it
runs -- so a capability that misreports its own authorization is still blocked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..capability_policy import Capability
from ..consequence_policy import ApprovalMode, ConsequenceAwareApprovalPolicy
from ..execution_audit import append_execution_record, verify_execution_audit
from ..execution_checkpoint import ExecutionCheckpointStore
from ..prompt_injection_guard import TrustLevel
from ..tool_registry import REGISTRY, ToolRegistry
from .authorization import CapabilityAuthorizationBroker
from .catalog import CapabilityCatalog
from .contract import CapabilityAuditRecord, CapabilityRequest
from .intent import DigitalGoal, understand_goal
from .planner import CapabilityPlan, CapabilityPlanner
from .provider import redact_secret_material


class DigitalResultState(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    BLOCKED = "blocked"
    REQUIRES_APPROVAL = "requires_approval"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True)
class StepRecord:
    """Durable, non-secret record of one executed capability step."""

    step_id: str
    capability_id: str
    tool_name: str
    state: str
    reason: str
    attempts: int
    verified: bool
    resumed: bool = False
    observation: str = ""

    def safe_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "capability_id": self.capability_id,
            "tool_name": self.tool_name,
            "state": self.state,
            "reason": self.reason,
            "attempts": self.attempts,
            "verified": self.verified,
            "resumed": self.resumed,
            "observation": self.observation,
        }


@dataclass(frozen=True)
class DigitalResult:
    """The final, auditable outcome of one digital goal."""

    state: DigitalResultState
    reason: str
    goal_digest: str
    plan_digest: str
    execution_id: str
    steps: tuple[StepRecord, ...]
    granted: tuple[Capability, ...]
    attempts: int
    audit_path: str
    requires_approval: bool = False
    resumed_steps: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        return self.state is DigitalResultState.VERIFIED

    def safe_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "goal_digest": self.goal_digest,
            "plan_digest": self.plan_digest,
            "execution_id": self.execution_id,
            "granted": tuple(item.value for item in self.granted),
            "attempts": self.attempts,
            "audit_path": self.audit_path,
            "requires_approval": self.requires_approval,
            "resumed_steps": tuple(self.resumed_steps),
            "steps": tuple(step.safe_dict() for step in self.steps),
        }


class DigitalAgentRuntime:
    """Bounded general-purpose digital agent runtime."""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        *,
        planner: CapabilityPlanner | None = None,
        authorization: CapabilityAuthorizationBroker | None = None,
        tool_registry: ToolRegistry = REGISTRY,
        approval_policy: ConsequenceAwareApprovalPolicy | None = None,
    ) -> None:
        self._catalog = catalog
        self._authorization = authorization or CapabilityAuthorizationBroker(
            catalog, tool_registry=tool_registry
        )
        self._planner = planner or CapabilityPlanner(catalog, authorization=self._authorization)
        self._tool_registry = tool_registry
        self._approval_policy = approval_policy or ConsequenceAwareApprovalPolicy()

    @property
    def catalog(self) -> CapabilityCatalog:
        return self._catalog

    @property
    def planner(self) -> CapabilityPlanner:
        return self._planner

    # -- public lifecycle -------------------------------------------------
    def understand(self, goal: DigitalGoal | str):
        """Step 1-2: goal -> intent understanding (no planning, no execution)."""
        resolved = goal if isinstance(goal, DigitalGoal) else DigitalGoal(str(goal))
        return understand_goal(resolved, self._catalog)

    def plan(self, goal: DigitalGoal | str, **kwargs: Any) -> CapabilityPlan:
        """Step 3-4: intent -> capability plan with authorization annotations."""
        return self._planner.plan(goal, **kwargs)

    def run(
        self,
        goal: DigitalGoal | str,
        *,
        root: Path | str,
        audit_path: Path | str,
        execution_id: str,
        requests: Mapping[str, Mapping[str, Any]] | None = None,
        granted: Iterable[Capability | str] = (),
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
        origin_trust: TrustLevel = TrustLevel.USER,
        checkpoint_path: Path | str | None = None,
        resume: bool = False,
    ) -> DigitalResult:
        """Execute the full lifecycle for one goal.

        ``requests`` maps a capability id to the concrete arguments for that
        capability. The goal says *what* the user wants; the catalog and planner
        decide *which* capabilities run; ``requests`` supplies their data.
        """
        audit = Path(audit_path)
        resolved = goal if isinstance(goal, DigitalGoal) else DigitalGoal(str(goal))
        payloads = dict(requests or {})

        if not execution_id.strip():
            return self._terminal(
                DigitalResultState.BLOCKED, "execution identity is required", resolved, "", execution_id, audit
            )
        if not verify_execution_audit(audit):
            return self._terminal(
                DigitalResultState.BLOCKED, "execution audit chain is invalid", resolved, "", execution_id, audit
            )
        if resume and self._unfinished(audit, execution_id):
            return self._terminal(
                DigitalResultState.RECOVERY_REQUIRED,
                "interrupted execution requires fresh authorization; automatic replay is disabled",
                resolved,
                "",
                execution_id,
                audit,
            )

        plan = self._planner.plan(
            resolved,
            granted=granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
            origin_trust=origin_trust,
        )

        if not plan.executable:
            state = (
                DigitalResultState.REQUIRES_APPROVAL
                if plan.requires_approval
                else DigitalResultState.BLOCKED
            )
            self._append(
                audit,
                CapabilityAuditRecord(
                    execution_id, "", "", "plan_blocked", state.value, plan.reason
                ),
            )
            return DigitalResult(
                state,
                plan.reason,
                resolved.digest,
                plan.plan_digest,
                execution_id,
                tuple(
                    StepRecord(step.step_id, step.capability_id, step.tool_name, "blocked", step.reason, 0, False)
                    for step in plan.steps
                ),
                plan.granted,
                0,
                str(audit),
                plan.requires_approval,
            )

        if plan.requires_approval and not explicitly_approved:
            self._append(
                audit,
                CapabilityAuditRecord(
                    execution_id,
                    "",
                    "",
                    "approval_required",
                    DigitalResultState.REQUIRES_APPROVAL.value,
                    "plan contains actions with meaningful side effects",
                ),
            )
            return DigitalResult(
                DigitalResultState.REQUIRES_APPROVAL,
                "plan contains actions with meaningful side effects and requires human approval",
                resolved.digest,
                plan.plan_digest,
                execution_id,
                tuple(
                    StepRecord(step.step_id, step.capability_id, step.tool_name, "pending_approval", step.reason, 0, False)
                    for step in plan.steps
                ),
                plan.granted,
                0,
                str(audit),
                True,
            )

        authorization_digest = self._authorization_digest(plan, explicitly_approved)
        store = ExecutionCheckpointStore(checkpoint_path) if checkpoint_path else None
        already_verified = (
            self._resumable_steps(store, plan, audit, execution_id, authorization_digest)
            if resume
            else set()
        )

        records: list[StepRecord] = []
        total_attempts = 0
        for step in plan.steps:
            if step.step_id in already_verified:
                records.append(
                    StepRecord(
                        step.step_id,
                        step.capability_id,
                        step.tool_name,
                        "verified",
                        "step was verified in a prior execution and was not replayed",
                        0,
                        True,
                        resumed=True,
                    )
                )
                continue

            capability = self._catalog.get(step.capability_id)
            if capability is None:
                # Unknown capability: fail closed, never substitute a default.
                self._append(
                    audit,
                    CapabilityAuditRecord(
                        execution_id, step.capability_id, step.tool_name, "capability_missing",
                        DigitalResultState.BLOCKED.value, "capability is not registered",
                    ),
                )
                return DigitalResult(
                    DigitalResultState.BLOCKED,
                    f"capability is not registered: {step.capability_id}",
                    resolved.digest,
                    plan.plan_digest,
                    execution_id,
                    tuple(records),
                    plan.granted,
                    total_attempts,
                    str(audit),
                    plan.requires_approval,
                    tuple(sorted(already_verified)),
                )

            # Authoritative re-check. The capability's own authorize() is a
            # query; the registry is the only source of truth.
            decision = self._tool_registry.authorize(
                step.tool_name,
                plan.granted,
                explicitly_approved=explicitly_approved,
                sandbox_available=sandbox_available,
                audit_available=audit_available,
            )
            approval = self._approval_policy.evaluate(
                capability.spec,
                origin_trust=origin_trust,
                explicitly_approved=explicitly_approved,
            )
            if not decision.allowed:
                return self._step_blocked(
                    audit, execution_id, step, decision.reason, records, resolved, plan,
                    total_attempts, already_verified,
                )
            if approval.mode is ApprovalMode.DENY:
                return self._step_blocked(
                    audit, execution_id, step, "consequence-aware policy denies this action",
                    records, resolved, plan, total_attempts, already_verified,
                )
            if approval.mode is ApprovalMode.REQUIRE_APPROVAL and not explicitly_approved:
                return self._step_blocked(
                    audit, execution_id, step,
                    "action has meaningful side effects and requires approval",
                    records, resolved, plan, total_attempts, already_verified,
                    state=DigitalResultState.REQUIRES_APPROVAL,
                )

            arguments = payloads.get(step.capability_id, payloads.get(step.tool_name, {}))
            reference = payloads.get(f"{step.capability_id}:credref")
            request = CapabilityRequest(
                step.capability_id,
                step.tool_name,
                dict(arguments),
                str(reference) if isinstance(reference, str) else None,
                approved=bool(explicitly_approved),
            )

            validation = capability.validate_input(request.arguments)
            if not validation.ok:
                return self._step_blocked(
                    audit, execution_id, step, validation.reason, records, resolved, plan,
                    total_attempts, already_verified,
                )

            capability.bind_audit_sink(
                lambda payload: append_execution_record(audit, dict(payload))
            )
            capability.audit(
                CapabilityAuditRecord(
                    execution_id, step.capability_id, step.tool_name, "capability_started",
                    "running", step_id=step.step_id,
                )
            )
            outcome = capability.bounded_retry(request)
            total_attempts += outcome.attempts
            observation = ""
            if outcome.observation is not None:
                observation = str(redact_secret_material(outcome.observation.detail))
            record = StepRecord(
                step.step_id,
                step.capability_id,
                step.tool_name,
                outcome.state,
                str(redact_secret_material(outcome.reason)),
                outcome.attempts,
                outcome.verified,
                observation=observation,
            )
            records.append(record)
            self._append(
                audit,
                CapabilityAuditRecord(
                    execution_id,
                    step.capability_id,
                    step.tool_name,
                    "capability_result",
                    outcome.state,
                    record.reason,
                    outcome.attempts,
                    outcome.verified,
                    step.step_id,
                ),
            )
            if store is not None:
                store.save(
                    execution_id=execution_id,
                    task_digest=resolved.digest,
                    plan_digest=plan.plan_digest,
                    authorization_digest=authorization_digest,
                    state=(
                        DigitalResultState.VERIFIED.value
                        if outcome.verified
                        else DigitalResultState.FAILED.value
                    ),
                    completed_step_ids=tuple(
                        item.step_id for item in records if item.verified
                    ),
                    total_attempts=total_attempts,
                )
            if not outcome.verified:
                if store is not None:
                    store.save(
                        execution_id=execution_id,
                        task_digest=resolved.digest,
                        plan_digest=plan.plan_digest,
                        authorization_digest=authorization_digest,
                        state=DigitalResultState.FAILED.value,
                        completed_step_ids=tuple(item.step_id for item in records if item.verified),
                        total_attempts=total_attempts,
                    )
                self._append(
                    audit,
                    CapabilityAuditRecord(
                        execution_id, step.capability_id, step.tool_name, "run_failed",
                        DigitalResultState.FAILED.value, record.reason, outcome.attempts, False,
                    ),
                )
                return DigitalResult(
                    DigitalResultState.FAILED,
                    f"capability step did not verify: {step.capability_id}: {record.reason}",
                    resolved.digest,
                    plan.plan_digest,
                    execution_id,
                    tuple(records),
                    plan.granted,
                    total_attempts,
                    str(audit),
                    plan.requires_approval,
                    tuple(sorted(already_verified)),
                )

        if store is not None:
            store.save(
                execution_id=execution_id,
                task_digest=resolved.digest,
                plan_digest=plan.plan_digest,
                authorization_digest=authorization_digest,
                state=DigitalResultState.VERIFIED.value,
                completed_step_ids=tuple(item.step_id for item in records if item.verified),
                total_attempts=total_attempts,
            )
        self._append(
            audit,
            CapabilityAuditRecord(
                execution_id, "", "", "run_verified", DigitalResultState.VERIFIED.value,
                "every capability step produced verified evidence", total_attempts, True,
            ),
        )
        return DigitalResult(
            DigitalResultState.VERIFIED,
            "every capability step executed and was verified with observable evidence",
            resolved.digest,
            plan.plan_digest,
            execution_id,
            tuple(records),
            plan.granted,
            total_attempts,
            str(audit),
            plan.requires_approval,
            tuple(sorted(already_verified)),
        )

    # -- internals --------------------------------------------------------
    def _step_blocked(
        self,
        audit: Path,
        execution_id: str,
        step: Any,
        reason: str,
        records: list[StepRecord],
        goal: DigitalGoal,
        plan: CapabilityPlan,
        attempts: int,
        already_verified: set[str],
        *,
        state: DigitalResultState = DigitalResultState.BLOCKED,
    ) -> DigitalResult:
        records.append(
            StepRecord(step.step_id, step.capability_id, step.tool_name, state.value.lower(), reason, 0, False)
        )
        self._append(
            audit,
            CapabilityAuditRecord(
                execution_id, step.capability_id, step.tool_name, "step_blocked", state.value, reason
            ),
        )
        return DigitalResult(
            state,
            f"{step.capability_id}: {reason}",
            goal.digest,
            plan.plan_digest,
            execution_id,
            tuple(records),
            plan.granted,
            attempts,
            str(audit),
            plan.requires_approval,
            tuple(sorted(already_verified)),
        )

    def _terminal(
        self,
        state: DigitalResultState,
        reason: str,
        goal: DigitalGoal,
        plan_digest: str,
        execution_id: str,
        audit: Path,
    ) -> DigitalResult:
        if execution_id.strip():
            self._append(
                audit,
                CapabilityAuditRecord(execution_id, "", "", "run_blocked", state.value, reason),
            )
        return DigitalResult(
            state, reason, goal.digest, plan_digest, execution_id, (), (), 0, str(audit)
        )

    def _append(self, audit: Path, record: CapabilityAuditRecord) -> None:
        """Append to the hash-chained execution audit.

        A corrupted chain is never extended: the run has already been reported
        as blocked, and writing onto a tampered chain would make the audit
        untrustworthy rather than preserving the evidence of tampering.
        """
        if not verify_execution_audit(audit):
            return
        append_execution_record(audit, record.as_record())

    def _authorization_digest(self, plan: CapabilityPlan, explicitly_approved: bool) -> str:
        import hashlib

        payload = {
            "granted": sorted(item.value for item in plan.granted),
            "explicitly_approved": bool(explicitly_approved),
            "steps": [
                {
                    "capability_id": step.capability_id,
                    "tool_name": step.tool_name,
                    "risk": step.risk,
                    "read_write": step.read_write,
                    "approval": step.approval,
                }
                for step in plan.steps
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _unfinished(audit: Path, execution_id: str) -> bool:
        if not audit.exists():
            return False
        terminal = {
            DigitalResultState.VERIFIED.value,
            DigitalResultState.FAILED.value,
            DigitalResultState.BLOCKED.value,
            DigitalResultState.REQUIRES_APPROVAL.value,
        }
        last: str | None = None
        for line in audit.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict) and item.get("execution_id") == execution_id:
                last = str(item.get("state", ""))
        return last is not None and last not in terminal

    def _verified_step_ids(self, audit: Path, execution_id: str) -> set[str]:
        if not audit.exists():
            return set()
        verified: set[str] = set()
        for line in audit.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(item, dict) or item.get("execution_id") != execution_id:
                continue
            if (
                item.get("event") == "capability_result"
                and item.get("result") == "success"
                and item.get("verification") == "verified"
                and item.get("step_id")
            ):
                verified.add(str(item["step_id"]))
        return verified

    def _resumable_steps(
        self,
        store: ExecutionCheckpointStore | None,
        plan: CapabilityPlan,
        audit: Path,
        execution_id: str,
        authorization_digest: str,
    ) -> set[str]:
        """Steps that were already verified under an identical plan/authorization.

        Resume never replays verified work, and never reuses progress from a
        different plan or a different authorization context.
        """
        if store is None:
            return set()
        try:
            checkpoint = store.load()
        except ValueError:
            return set()
        if checkpoint is None or checkpoint.execution_id != execution_id:
            return set()
        if checkpoint.plan_digest != plan.plan_digest:
            return set()
        if checkpoint.authorization_digest != authorization_digest:
            return set()
        planned = {step.step_id for step in plan.steps}
        return {
            step_id
            for step_id in self._verified_step_ids(audit, execution_id)
            if step_id in planned and step_id in set(checkpoint.completed_step_ids)
        }


__all__ = [
    "DigitalAgentRuntime",
    "DigitalResult",
    "DigitalResultState",
    "StepRecord",
]
