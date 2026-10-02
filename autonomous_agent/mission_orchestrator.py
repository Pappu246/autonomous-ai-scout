from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .capability_policy import Capability
from .task_dag import DAGNode, DAGTaskSpec, LongHorizonPlanner, TaskDAGPlan
from .task_planner import default_grants_for_task
from .execution_engine import ExecutionResult, ExecutionState


@dataclass(frozen=True)
class MissionStepResult:
    node_id: str
    task: str
    state: str
    reason: str
    attempts: int


@dataclass(frozen=True)
class MissionExecutionResult:
    state: str
    reason: str
    completed_steps: tuple[str, ...]
    failed_step: str | None
    results: tuple[MissionStepResult, ...]

    @property
    def success(self) -> bool:
        return self.state == ExecutionState.VERIFIED.value


StepRunner = Callable[[DAGNode], ExecutionResult]


class MissionOrchestrator:
    """Compile and execute bounded mission steps over the existing task DAG."""

    def __init__(self, *, planner: LongHorizonPlanner | None = None) -> None:
        self.planner = planner or LongHorizonPlanner()

    @staticmethod
    def sequential_specs(tasks: Iterable[str]) -> tuple[DAGTaskSpec, ...]:
        normalized = tuple(" ".join(str(task).split()) for task in tasks if str(task).strip())
        if not normalized:
            return ()
        return tuple(
            DAGTaskSpec(
                node_id=f"step-{index}",
                task=task,
                depends_on=() if index == 1 else (f"step-{index - 1}",),
            )
            for index, task in enumerate(normalized, 1)
        )

    @staticmethod
    def required_grants(specs: Iterable[DAGTaskSpec]) -> tuple[Capability, ...]:
        values: list[Capability] = []
        seen: set[Capability] = set()
        for spec in specs:
            for capability in default_grants_for_task(spec.task):
                if capability not in seen:
                    seen.add(capability)
                    values.append(capability)
        return tuple(values)

    def prepare(
        self,
        objective: str,
        tasks: Iterable[str],
        *,
        explicitly_approved: bool = False,
    ) -> TaskDAGPlan:
        specs = self.sequential_specs(tasks)
        if not specs:
            return self.planner.plan(objective, (), explicitly_approved=explicitly_approved)
        return self.planner.plan(
            objective,
            specs,
            granted=self.required_grants(specs),
            explicitly_approved=explicitly_approved,
        )

    def execute(
        self,
        plan: TaskDAGPlan,
        *,
        completed_steps: Iterable[str] = (),
        runner: StepRunner,
    ) -> MissionExecutionResult:
        if not plan.executable:
            return MissionExecutionResult(
                state=ExecutionState.BLOCKED.value,
                reason=plan.reason,
                completed_steps=tuple(completed_steps),
                failed_step=None,
                results=(),
            )

        completed = set(completed_steps)
        results: list[MissionStepResult] = []
        for node_id in plan.topological_order():
            node = next(node for node in plan.nodes if node.node_id == node_id)
            if node_id in completed:
                continue
            if any(dep not in completed for dep in node.depends_on):
                return MissionExecutionResult(
                    state=ExecutionState.BLOCKED.value,
                    reason=f"dependencies for {node_id} are not verified",
                    completed_steps=tuple(sorted(completed)),
                    failed_step=node_id,
                    results=tuple(results),
                )
            try:
                result = runner(node)
            except Exception as exc:
                result = ExecutionResult(
                    ExecutionState.FAILED,
                    f"mission step runner raised {type(exc).__name__}",
                    0,
                    (),
                    "",
                )
            step_result = MissionStepResult(
                node_id=node_id,
                task=node.task,
                state=result.state.value,
                reason=result.reason,
                attempts=int(getattr(result, "attempts", 0) or 0),
            )
            results.append(step_result)
            if result.state is not ExecutionState.VERIFIED:
                return MissionExecutionResult(
                    state=result.state.value,
                    reason=f"{node_id}: {result.reason}",
                    completed_steps=tuple(sorted(completed)),
                    failed_step=node_id,
                    results=tuple(results),
                )
            completed.add(node_id)

        return MissionExecutionResult(
            state=ExecutionState.VERIFIED.value,
            reason="all mission steps were independently verified",
            completed_steps=tuple(sorted(completed)),
            failed_step=None,
            results=tuple(results),
        )


__all__ = [
    "MissionExecutionResult",
    "MissionOrchestrator",
    "MissionStepResult",
]
