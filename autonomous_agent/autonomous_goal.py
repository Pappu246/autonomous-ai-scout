from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Protocol

from .capability_policy import Capability
from .task_orchestrator import OrchestrationState, StructuredTask, TaskOrchestrator


MAX_STEPS = 12
MAX_RETRIES_PER_STEP = 2


class GoalState(str, Enum):
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    ADAPTING = "adapting"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class GoalStep:
    step_id: str
    description: str
    tool_name: str
    capability: Capability
    requires_approval: bool = False


@dataclass(frozen=True)
class GoalPlan:
    objective: str
    steps: tuple[GoalStep, ...]
    plan_digest: str


@dataclass(frozen=True)
class StepObservation:
    success: bool
    output: object = None
    verification: str = ""


@dataclass(frozen=True)
class GoalResult:
    objective: str
    state: GoalState
    completed_steps: tuple[str, ...]
    attempts: int
    reason: str
    observations: tuple[StepObservation, ...] = ()


class GoalPlanner(Protocol):
    def plan(self, task: StructuredTask, observations: Iterable[StepObservation] = ()) -> GoalPlan: ...


class GoalExecutor(Protocol):
    def execute(self, step: GoalStep) -> StepObservation: ...


class GoalVerifier(Protocol):
    def verify(self, task: StructuredTask, step: GoalStep, observation: StepObservation) -> bool: ...


class AutonomousGoalRunner:
    """Bounded observe -> execute -> verify -> adapt loop.

    Planning and execution remain separate: the planner chooses registered
    capabilities, while the executor remains responsible for the existing
    sandbox/approval/lifecycle boundaries.
    """

    def __init__(
        self,
        *,
        orchestrator: TaskOrchestrator,
        planner: GoalPlanner,
        executor: GoalExecutor,
        verifier: GoalVerifier,
        max_steps: int = MAX_STEPS,
        max_retries: int = MAX_RETRIES_PER_STEP,
    ):
        self.orchestrator = orchestrator
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.max_steps = max(1, min(int(max_steps), MAX_STEPS))
        self.max_retries = max(0, min(int(max_retries), MAX_RETRIES_PER_STEP))

    def run(
        self,
        task: StructuredTask | str,
        *,
        granted: Iterable[Capability | str] = (),
        explicitly_approved: bool = False,
    ) -> GoalResult:
        safe, initial = self.orchestrator.plan(
            task,
            granted=granted,
            explicitly_approved=explicitly_approved,
        )
        if not initial.executable:
            return GoalResult(safe.task, GoalState.BLOCKED, (), 0, initial.reason)

        plan = self.planner.plan(safe)
        if not plan.steps:
            return GoalResult(safe.task, GoalState.FAILED, (), 0, "planner produced no executable steps")
        if len(plan.steps) > self.max_steps:
            return GoalResult(safe.task, GoalState.BLOCKED, (), 0, "goal exceeds bounded step limit")

        completed: list[str] = []
        observations: list[StepObservation] = []
        attempts = 0

        index = 0
        while index < len(plan.steps):
            step = plan.steps[index]
            if step.requires_approval and not explicitly_approved:
                return GoalResult(
                    safe.task,
                    GoalState.WAITING_APPROVAL,
                    tuple(completed),
                    attempts,
                    f"approval required before step {step.step_id}",
                    tuple(observations),
                )

            verified = False
            last_observation = StepObservation(False, None, "not executed")
            for _ in range(self.max_retries + 1):
                attempts += 1
                last_observation = self.executor.execute(step)
                observations.append(last_observation)
                if last_observation.success and self.verifier.verify(safe, step, last_observation):
                    verified = True
                    break

            if verified:
                completed.append(step.step_id)
                index += 1
                continue

            # The failure is fed back into planning. The replanned goal may
            # recover, choose another tool, or terminate safely.
            replanned = self.planner.plan(safe, tuple(observations))
            if not replanned.steps or len(replanned.steps) > self.max_steps:
                return GoalResult(
                    safe.task,
                    GoalState.FAILED,
                    tuple(completed),
                    attempts,
                    f"verification failed for {step.step_id} and replanning produced no bounded recovery",
                    tuple(observations),
                )
            plan = replanned
            index = 0
            completed.clear()

        return GoalResult(
            safe.task,
            GoalState.COMPLETED,
            tuple(completed),
            attempts,
            "goal completed and every executed step was verified",
            tuple(observations),
        )
