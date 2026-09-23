from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from .capability_policy import Capability
from .task_plan_models import TaskPlan
from .task_planner import plan_task
from .tool_registry import REGISTRY, ToolRegistry

MAX_DAG_NODES = 16


@dataclass(frozen=True)
class DAGTaskSpec:
    node_id: str
    task: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class DAGNode:
    node_id: str
    task: str
    depends_on: tuple[str, ...]
    plan: TaskPlan


@dataclass(frozen=True)
class TaskDAGPlan:
    objective: str
    nodes: tuple[DAGNode, ...]
    digest: str
    executable: bool
    reason: str

    def topological_order(self) -> tuple[str, ...]:
        remaining = {node.node_id: set(node.depends_on) for node in self.nodes}
        order: list[str] = []
        while remaining:
            ready = sorted(node_id for node_id, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError("task DAG contains a dependency cycle")
            order.extend(ready)
            for node_id in ready:
                remaining.pop(node_id)
            for deps in remaining.values():
                deps.difference_update(ready)
        return tuple(order)

    @property
    def ready_frontier(self) -> tuple[str, ...]:
        return tuple(node.node_id for node in self.nodes if not node.depends_on)


@dataclass(frozen=True)
class DAGExecutionResult:
    """Result of one bounded sequential DAG execution attempt."""
    completed: tuple[str, ...]
    blocked: tuple[str, ...]
    failed_node: str | None

    @property
    def is_success(self) -> bool:
        """True only when every DAG node completed and none failed."""
        return not self.blocked and self.failed_node is None

    @property
    def success(self) -> bool:
        """Backward-compatible alias for is_success."""
        return self.is_success


def _digest(objective: str, nodes: Iterable[DAGNode]) -> str:
    payload = {
        "objective": objective,
        "nodes": [
            {
                "depends_on": node.depends_on,
                "node_id": node.node_id,
                "plan_digest": node.plan.audit.plan_digest,
                "task": node.task,
            }
            for node in nodes
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _validate_specs(specs: tuple[DAGTaskSpec, ...]) -> str | None:
    if not specs:
        return "task DAG must contain at least one node"
    if len(specs) > MAX_DAG_NODES:
        return f"task DAG exceeds bounded node limit of {MAX_DAG_NODES}"
    ids = [spec.node_id for spec in specs]
    if any(not node_id.strip() for node_id in ids):
        return "task DAG node ids must be non-empty"
    if len(set(ids)) != len(ids):
        return "task DAG contains duplicate node ids"
    known = set(ids)
    for spec in specs:
        missing = tuple(dep for dep in spec.depends_on if dep not in known)
        if missing:
            return f"node {spec.node_id} references missing dependencies: {', '.join(missing)}"
        if spec.node_id in spec.depends_on:
            return f"node {spec.node_id} cannot depend on itself"
    return None


class LongHorizonPlanner:
    """Build a bounded dependency DAG from canonical task-plan nodes."""

    def __init__(self, registry: ToolRegistry = REGISTRY) -> None:
        self._registry = registry

    def plan(
        self,
        objective: str,
        specs: Iterable[DAGTaskSpec],
        *,
        granted: Iterable[Capability | str] = (),
        explicitly_approved: bool = False,
        sandbox_available: bool = True,
        audit_available: bool = True,
    ) -> TaskDAGPlan:
        normalized = tuple(
            DAGTaskSpec(
                " ".join(spec.node_id.strip().split()),
                " ".join(spec.task.strip().split()),
                tuple(dict.fromkeys(spec.depends_on)),
            )
            for spec in specs
        )
        error = _validate_specs(normalized)
        if error:
            return TaskDAGPlan(objective.strip(), (), "", False, error)

        nodes: list[DAGNode] = []
        for spec in normalized:
            plan = plan_task(
                spec.task,
                granted=granted,
                explicitly_approved=explicitly_approved,
                sandbox_available=sandbox_available,
                audit_available=audit_available,
                registry=self._registry,
            )
            nodes.append(DAGNode(spec.node_id, spec.task, spec.depends_on, plan))

        dag = TaskDAGPlan(objective.strip(), tuple(nodes), "", all(node.plan.executable for node in nodes), "all DAG nodes are executable")
        try:
            dag.topological_order()
        except ValueError:
            return TaskDAGPlan(dag.objective, dag.nodes, "", False, "task DAG contains a dependency cycle")
        if not dag.executable:
            blocked = tuple(node.node_id for node in dag.nodes if not node.plan.executable)
            return TaskDAGPlan(dag.objective, dag.nodes, _digest(dag.objective, dag.nodes), False, f"DAG contains non-executable nodes: {', '.join(blocked)}")
        return TaskDAGPlan(dag.objective, dag.nodes, _digest(dag.objective, dag.nodes), True, dag.reason)


def execute_dag(
    dag: TaskDAGPlan,
    runner: Callable[[DAGNode], bool],
) -> DAGExecutionResult:
    if not dag.executable:
        return DAGExecutionResult((), tuple(node.node_id for node in dag.nodes), None)
    node_by_id = {node.node_id: node for node in dag.nodes}
    completed: list[str] = []
    blocked: list[str] = []
    for node_id in dag.topological_order():
        node = node_by_id[node_id]
        if any(dep in blocked for dep in node.depends_on):
            blocked.append(node_id)
            continue
        if any(dep not in completed for dep in node.depends_on):
            blocked.append(node_id)
            continue
        try:
            ok = bool(runner(node))
        except Exception:
            ok = False
        if not ok:
            failed = node_id
            affected = []
            descendants = {failed}
            changed = True
            while changed:
                changed = False
                for candidate in dag.nodes:
                    if candidate.node_id in descendants:
                        continue
                    if any(dep in descendants for dep in candidate.depends_on):
                        descendants.add(candidate.node_id)
                        changed = True
                        affected.append(candidate.node_id)
            return DAGExecutionResult(tuple(completed), tuple(blocked) + tuple(affected), failed)
        completed.append(node_id)
    return DAGExecutionResult(tuple(completed), tuple(blocked), None)


__all__ = [
    "DAGExecutionResult",
    "DAGNode",
    "DAGTaskSpec",
    "LongHorizonPlanner",
    "MAX_DAG_NODES",
    "TaskDAGPlan",
    "execute_dag",
]
