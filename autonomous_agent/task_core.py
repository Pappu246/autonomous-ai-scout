from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .capability_policy import Capability
from .adaptive_execution import AdaptiveExecutionResult, Replanner, execute_adaptive_plan
from .execution_engine import MAX_OUTPUT_BYTES, ExecutionResult, execute_plan
from .task_orchestrator import StructuredTask, TaskOrchestrator
from .task_plan_models import TaskPlan
from .task_planner import default_grants_for_task
from .tool_registry import REGISTRY, ToolRegistry


@dataclass(frozen=True)
class CanonicalTask:
    """Normalized task request plus its canonical plan and task identity."""

    request: StructuredTask
    plan: TaskPlan
    task_digest: str
    granted: tuple[Capability, ...] = field(default_factory=tuple)

    @property
    def task(self) -> str:
        return self.request.task

    @property
    def project(self) -> str | None:
        return self.request.project


class AutonomousTaskCore:
    """Single public planning/execution boundary for task-driven agent work.

    This phase deliberately composes the existing planner, policy, registry,
    sandbox and orchestration boundaries instead of creating second versions of
    them. Durable state, dynamic replanning and background execution belong to
    later phases.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry = REGISTRY,
        orchestrator: TaskOrchestrator | None = None,
    ) -> None:
        self._registry = registry
        self._orchestrator = orchestrator or TaskOrchestrator(registry=registry)

    @staticmethod
    def _digest(request: StructuredTask) -> str:
        payload = {
            "task": request.task,
            "project": request.project,
            "metadata": dict(request.metadata or {}),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_grants(
        granted: Iterable[Capability | str],
    ) -> tuple[Capability, ...]:
        values: list[Capability] = []
        seen: set[Capability] = set()
        for item in granted:
            capability = item if isinstance(item, Capability) else Capability(item)
            if capability not in seen:
                seen.add(capability)
                values.append(capability)
        return tuple(values)

    def prepare(
        self,
        task: StructuredTask | str,
        *,
        granted: Iterable[Capability | str] | None = None,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
    ) -> CanonicalTask:
        """Normalize one task and produce its only canonical plan."""
        selected_grants = (
            default_grants_for_task(task, self._registry)
            if granted is None
            else self._normalize_grants(granted)
        )
        request, plan = self._orchestrator.plan(
            task,
            granted=selected_grants,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_available=audit_available,
        )
        return CanonicalTask(
            request=request,
            plan=plan,
            task_digest=self._digest(request),
            granted=selected_grants,
        )

    def execute_adaptive(
        self,
        prepared: CanonicalTask,
        root: Path,
        *,
        audit_path: Path,
        execution_id: str,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        max_retries_per_step: int = 1,
        max_replans: int = 1,
        timeout_seconds: int = 30,
        output_limit: int = MAX_OUTPUT_BYTES,
        web_connector: Any = None,
        web_request: Mapping[str, Any] | None = None,
        workspace_connector: Any = None,
        workspace_request: Mapping[str, Any] | None = None,
        gmail_connector: Any = None,
        gmail_request: Mapping[str, Any] | None = None,
        calendar_connector: Any = None,
        calendar_request: Mapping[str, Any] | None = None,
        browser_connector: Any = None,
        browser_request: Mapping[str, Any] | None = None,
        replanner: Replanner | None = None,
    ) -> AdaptiveExecutionResult:
        """Execute with bounded observation, retry, and adaptive replanning."""
        return execute_adaptive_plan(
            prepared.plan,
            root,
            granted=prepared.granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_path=audit_path,
            execution_id=execution_id,
            registry=self._registry,
            max_retries_per_step=max_retries_per_step,
            max_replans=max_replans,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
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
            replanner=replanner,
        )

    def execute(
        self,
        prepared: CanonicalTask,
        root: Path,
        *,
        audit_path: Path,
        execution_id: str,
        checkpoint_path: Path | None = None,
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        max_retries: int = 0,
        timeout_seconds: int = 30,
        output_limit: int = MAX_OUTPUT_BYTES,
        memory: Any = None,
        project: str = "local",
        web_connector: Any = None,
        web_request: Mapping[str, Any] | None = None,
        workspace_connector: Any = None,
        workspace_request: Mapping[str, Any] | None = None,
        gmail_connector: Any = None,
        gmail_request: Mapping[str, Any] | None = None,
        calendar_connector: Any = None,
        calendar_request: Mapping[str, Any] | None = None,
        browser_connector: Any = None,
        browser_request: Mapping[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute the prepared plan through the existing execution boundary."""
        return execute_plan(
            prepared.plan,
            root,
            granted=prepared.granted,
            explicitly_approved=explicitly_approved,
            sandbox_available=sandbox_available,
            audit_path=audit_path,
            execution_id=execution_id,
            checkpoint_path=checkpoint_path,
            registry=self._registry,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            memory=memory,
            project=project,
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


__all__ = ["AdaptiveExecutionResult", "AutonomousTaskCore", "CanonicalTask"]
