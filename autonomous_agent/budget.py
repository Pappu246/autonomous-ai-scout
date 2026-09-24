from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


class BudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResourceBudget:
    attempts: int = 20
    tool_calls: int = 50
    wall_seconds: float = 300.0
    output_bytes: int = 1024 * 1024
    external_side_effects: int = 10

    def __post_init__(self) -> None:
        if self.attempts < 0 or self.tool_calls < 0 or self.wall_seconds < 0 or self.output_bytes < 0 or self.external_side_effects < 0:
            raise ValueError("resource budgets cannot be negative")


@dataclass(frozen=True)
class BudgetUsage:
    attempts: int = 0
    tool_calls: int = 0
    wall_seconds: float = 0.0
    output_bytes: int = 0
    external_side_effects: int = 0


class BudgetLedger:
    def __init__(self, budget: ResourceBudget):
        self.budget = budget
        self._usage = BudgetUsage()
        self._lock = Lock()

    @property
    def usage(self) -> BudgetUsage:
        with self._lock:
            return self._usage

    def consume(
        self,
        *,
        attempts: int = 0,
        tool_calls: int = 0,
        wall_seconds: float = 0.0,
        output_bytes: int = 0,
        external_side_effects: int = 0,
    ) -> BudgetUsage:
        with self._lock:
            next_usage = BudgetUsage(
                self._usage.attempts + max(0, attempts),
                self._usage.tool_calls + max(0, tool_calls),
                self._usage.wall_seconds + max(0.0, wall_seconds),
                self._usage.output_bytes + max(0, output_bytes),
                self._usage.external_side_effects + max(0, external_side_effects),
            )
            checks = (
                (next_usage.attempts, self.budget.attempts, "attempt budget"),
                (next_usage.tool_calls, self.budget.tool_calls, "tool-call budget"),
                (next_usage.wall_seconds, self.budget.wall_seconds, "wall-clock budget"),
                (next_usage.output_bytes, self.budget.output_bytes, "output-byte budget"),
                (next_usage.external_side_effects, self.budget.external_side_effects, "external side-effect budget"),
            )
            for actual, limit, label in checks:
                if actual > limit:
                    raise BudgetExceededError(f"{label} exceeded")
            self._usage = next_usage
            return next_usage


__all__ = ["BudgetExceededError", "BudgetLedger", "BudgetUsage", "ResourceBudget"]
