from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .capability_policy import Capability
from .execution_engine import ExecutionResult, ExecutionState, execute_plan
from .execution_audit import append_execution_record
from .sandbox import MAX_OUTPUT_BYTES, SandboxResult
from .task_plan_models import PlanRisk, TaskAuditRecord, TaskPlan, TaskStep
from .tool_registry import REGISTRY, ToolRegistry


@dataclass(frozen=True)
class StepObservation:
    step_id: str
    tool_name: str
    state: ExecutionState
    success: bool
    verification_status: str
    attempts: int
    exit_status: int | None
    output_truncated: bool

    @property
    outcome(self) -> str:
        return "verified" if self.success and self.verification_status == "verified" else "failed"


@dataclass(frozen=True)
class AdaptiveExecutionResult:
    state: ExecutionState
    reason: str
    attempts: int
    replans: int
    results: tuple[SandboxResult, ...]
    observations: tuple[StepObservation, ...]
    audit_path: str


Replanner = Callable[[StepObservation, tuple[TaskStep, ...]], Iterable[TaskStep] | None]


def _single_step_plan(parent: TaskPlan, step: TaskStep) -> TaskPlan:
    payload = {
        "intent": parent.intent.value,
        "step_id": step.step_id,
        "task": parent.task,
        "tool": step.tool_name,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return TaskPlan(
        task=parent.task,
        intent=parent.intent,
        steps=(step,),
        risk=step.risk,
        executable=True,
        reason="single-step child plan delegated to the existing execution boundary",
        audit=TaskAuditRecord(parent.task, parent.intent, (step.step_id,), True, digest),
    )


def _safe_observation(step: TaskStep, result: ExecutionResult) -> StepObservation:
    sandbox_result = result.results[-1] if result.results else None
    return StepObservation(
        step_id=step.step_id,
        tool_name=step.tool_name,
        state=result.state,
        success=bool(sandbox_result and sandbox_result.success and sandbox_result.verification_status == "verified"),
        verification_status=sandbox_result.verification_status if sandbox_result else "none",
        attempts=max(1, result.attempts),
        exit_status=sandbox_result.exit_status if sandbox_result else None,
        output_truncated=bool(sandbox_result and sandbox_result.output_truncated),
    )


def _audit(path: Path, execution_id: str, event: str, **fields: object) -> None:
    append_execution_record(
        path,
        {
            "execution_id": execution_id,
            "event": event,
            **{key: str(value) for key, value in fields.items()},
        },
    )


def execute_adaptive_plan(
    plan: TaskPlan,
    root: Path,
    *,
    granted: Iterable[Capability | str] = (),
    explicitly_approved: bool = False,
    sandbox_available: bool = True,
    audit_path: Path,
    execution_id: str,
    registry: ToolRegistry = REGISTRY,
    max_retries_per_step: int = 1,
    max_replans: int = 1,
    timeout_seconds: int = 30,
    output_limit: int = MAX_OUTPUT_BYTES,
    web_connector: object | None = None,
    web_request: Mapping[str, object] | None = None,
    workspace_connector: object | None = None,
    workspace_request: Mapping[str, object] | None = None,
    gmail_connector: object | None = None,
    gmail_request: Mapping[str, object] | None = None,
    calendar_connector: object | None = None,
    calendar_request: Mapping[str, object] | None = None,
    browser_connector: object | None = None,
    browser_request: Mapping[str, object] | None = None,
    replanner: Replanner | None = None,
) -> AdaptiveExecutionResult:
    if not execution_id.strip():
        return AdaptiveExecutionResult(ExecutionState.BLOCKED, "execution identity is required", 0, 0, (), (), str(audit_path))
    if not plan.executable:
        return AdaptiveExecutionResult(ExecutionState.BLOCKED, "task plan is not executable", 0, 0, (), (), str(audit_path))
    if max_retries_per_step < 0 or max_replans < 0:
        return AdaptiveExecutionResult(ExecutionState.BLOCKED, "adaptive budgets cannot be negative", 0, 0, (), (), str(audit_path))

    steps = list(plan.steps)
    results: list[SandboxResult] = []
    observations: list[StepObservation] = []
    total_attempts = 0
    replan_count = 0
    index = 0

    _audit(audit_path, execution_id, "execution_started", plan_digest=plan.audit.plan_digest)

    while index < len(steps):
        step = steps[index]
        last_observation: StepObservation | None = None
        completed = False

        for attempt in range(max_retries_per_step + 1):
            child_id = f"{execution_id}:{step.step_id}:attempt-{attempt + 1}"
            safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in child_id)
            child_audit = audit_path.with_name(f"{audit_path.stem}.{safe_id}.jsonl")
            child_checkpoint = child_audit.with_suffix(".checkpoint.json")
            child = execute_plan(
                _single_step_plan(plan, step),
                root,
                granted=granted,
                explicitly_approved=explicitly_approved,
                sandbox_available=sandbox_available,
                audit_path=child_audit,
                execution_id=child_id,
                registry=registry,
                max_retries=0,
                timeout_seconds=timeout_seconds,
                output_limit=output_limit,
                checkpoint_path=child_checkpoint,
                web_connector=web_connector,
                web_request=web_request,
                workspace_connector=workspace_connector,
                workspace_request=workspace_request,
                gmail_connector=gmail_connector,
                gmail_request=gmail_request,
                calendar_connector=calendar_connector,
                calendar_request=calendar_request,
                browser_connector=browser_connector,
                browser_request=browser_request,
            )
            total_attempts += max(1, child.attempts)
            results.extend(child.results)
            last_observation = _safe_observation(step, child)
            observations.append(last_observation)
            _audit(
                audit_path,
                execution_id,
                "OBSERVATION",
                step_id=step.step_id,
                tool=step.tool_name,
                outcome=last_observation.outcome,
                verification=last_observation.verification_status,
                attempt=attempt + 1,
            )

            if child.state is ExecutionState.VERIFIED and last_observation.success:
                completed = True
                _audit(audit_path, execution_id, "VERIFICATION", step_id=step.step_id, status="verified")
                break

            if child.state in {ExecutionState.BLOCKED, ExecutionState.RECOVERY_REQUIRED}:
                _audit(
                    audit_path,
                    execution_id,
                    "ADAPTATION_BLOCKED",
                    step_id=step.step_id,
                    reason=child.reason,
                )
                return AdaptiveExecutionResult(child.state, child.reason, total_attempts, replan_count, tuple(results), tuple(observations), str(audit_path))

            if attempt < max_retries_per_step:
                _audit(audit_path, execution_id, "RETRY", step_id=step.step_id, next_attempt=attempt + 2)

        if completed:
            _audit(audit_path, execution_id, "STEP_COMPLETED", step_id=step.step_id, tool=step.tool_name)
            index += 1
            continue

        if replanner is None or replan_count >= max_replans or last_observation is None:
            _audit(audit_path, execution_id, "TASK_FAILED", step_id=step.step_id, reason="retry budget exhausted and no adaptive replan available")
            return AdaptiveExecutionResult(ExecutionState.FAILED, f"adaptive recovery exhausted for {step.tool_name}", total_attempts, replan_count, tuple(results), tuple(observations), str(audit_path))

        replacement = tuple(replanner(last_observation, tuple(steps[index + 1:])))
        if not replacement:
            _audit(audit_path, execution_id, "TASK_FAILED", step_id=step.step_id, reason="replanner produced no replacement steps")
            return AdaptiveExecutionResult(ExecutionState.FAILED, f"replanner produced no safe replacement for {step.tool_name}", total_attempts, replan_count, tuple(results), tuple(observations), str(audit_path))

        existing_ids = {candidate.step_id for candidate in steps}
        if any(candidate.step_id in existing_ids and candidate.step_id != step.step_id for candidate in replacement):
            _audit(audit_path, execution_id, "TASK_FAILED", step_id=step.step_id, reason="replanner produced colliding step ids")
            return AdaptiveExecutionResult(ExecutionState.FAILED, "replanner produced colliding step ids", total_attempts, replan_count, tuple(results), tuple(observations), str(audit_path))

        steps[index:index + 1] = list(replacement)
        replan_count += 1
        _audit(
            audit_path,
            execution_id,
            "REPLAN",
            failed_step=step.step_id,
            replacement_steps=tuple(candidate.step_id for candidate in replacement),
            replan_count=replan_count,
        )

    _audit(audit_path, execution_id, "TASK_COMPLETED", attempts=total_attempts, replans=replan_count)
    return AdaptiveExecutionResult(
        ExecutionState.VERIFIED,
        "all adaptive steps executed and verified through the existing execution boundary",
        total_attempts,
        replan_count,
        tuple(results),
        tuple(observations),
        str(audit_path),
    )


__all__ = ["AdaptiveExecutionResult", "Replanner", "StepObservation", "execute_adaptive_plan"]
