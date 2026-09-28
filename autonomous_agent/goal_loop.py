from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


class GoalLoopError(ValueError):
    pass


@dataclass(frozen=True)
class GoalStep:
    name: str
    completed: bool


@dataclass(frozen=True)
class GoalRun:
    goal: str
    steps: tuple[GoalStep, ...]
    iterations: int
    stopped: bool
    reason: str


def run_goal_loop(
    goal: str,
    steps: Iterable[str],
    observe: Callable[[str], bool],
    *,
    max_iterations: int = 3,
) -> GoalRun:
    normalized_goal = " ".join(str(goal).split())
    if not normalized_goal or len(normalized_goal) > 2000:
        raise GoalLoopError("goal is invalid")
    if max_iterations < 1 or max_iterations > 10:
        raise GoalLoopError("goal loop iteration budget is invalid")
    names = tuple(" ".join(str(item).split()) for item in steps if str(item).strip())
    if not names:
        raise GoalLoopError("goal requires at least one bounded step")
    latest = tuple(GoalStep(name, False) for name in names)
    for iteration in range(1, max_iterations + 1):
        latest = tuple(GoalStep(step.name, bool(observe(step.name))) for step in latest)
        if all(step.completed for step in latest):
            return GoalRun(normalized_goal, latest, iteration, False, "goal reached")
    return GoalRun(normalized_goal, latest, max_iterations, True, "goal loop iteration limit reached")


__all__ = ["GoalLoopError", "GoalRun", "GoalStep", "run_goal_loop"]
