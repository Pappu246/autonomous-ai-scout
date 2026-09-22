from pathlib import Path

from autonomous_agent.capability_policy import Capability
from autonomous_agent.task_dag import DAGTaskSpec, LongHorizonPlanner, execute_dag


def test_dag_builds_deterministic_dependency_order():
    planner = LongHorizonPlanner()
    specs = (
        DAGTaskSpec("inspect", "inspect repository"),
        DAGTaskSpec("research", "research this topic"),
        DAGTaskSpec("test", "run tests", ("inspect",)),
    )
    first = planner.plan(
        "verify project",
        specs,
        granted=[Capability.INSPECT, Capability.TEST, Capability.WEB_RESEARCH],
    )
    second = planner.plan(
        "verify project",
        specs,
        granted=[Capability.INSPECT, Capability.TEST, Capability.WEB_RESEARCH],
    )

    assert first.executable
    assert first.digest == second.digest
    assert first.topological_order() == ("inspect", "research", "test")
    assert first.ready_frontier == ("inspect", "research")


def test_dag_rejects_missing_dependency():
    result = LongHorizonPlanner().plan(
        "invalid",
        (DAGTaskSpec("test", "run tests", ("missing",)),),
        granted=[Capability.INSPECT, Capability.TEST],
    )
    assert not result.executable
    assert "missing dependencies" in result.reason


def test_dag_rejects_cycle():
    result = LongHorizonPlanner().plan(
        "cycle",
        (
            DAGTaskSpec("a", "inspect repository", ("b",)),
            DAGTaskSpec("b", "inspect repository", ("a",)),
        ),
        granted=[Capability.INSPECT],
    )
    assert not result.executable
    assert "cycle" in result.reason


def test_dag_preserves_policy_blocked_nodes():
    result = LongHorizonPlanner().plan(
        "change project",
        (
            DAGTaskSpec("change", "fix the failing tests"),
        ),
        granted=[Capability.INSPECT, Capability.TEST],
    )
    assert not result.executable
    assert result.nodes[0].plan.executable is False


def test_dag_scheduler_blocks_downstream_after_failure():
    planner = LongHorizonPlanner()
    dag = planner.plan(
        "verify project",
        (
            DAGTaskSpec("inspect", "inspect repository"),
            DAGTaskSpec("test", "run tests", ("inspect",)),
            DAGTaskSpec("research", "research this topic"),
        ),
        granted=[Capability.INSPECT, Capability.TEST, Capability.WEB_RESEARCH],
    )
    assert dag.executable

    calls: list[str] = []

    def runner(node):
        calls.append(node.node_id)
        return node.node_id != "inspect"

    result = execute_dag(dag, runner)

    assert result.success is False
    assert result.completed == ()
    assert result.failed_node == "inspect"
    assert "test" in result.blocked
    assert calls == ["inspect"]


def test_dag_scheduler_executes_ready_nodes_in_dependency_order(tmp_path: Path):
    dag = LongHorizonPlanner().plan(
        "verify project",
        (
            DAGTaskSpec("inspect", "inspect repository"),
            DAGTaskSpec("research", "research this topic"),
            DAGTaskSpec("test", "run tests", ("inspect",)),
        ),
        granted=[Capability.INSPECT, Capability.TEST, Capability.WEB_RESEARCH],
    )
    calls: list[str] = []
    result = execute_dag(dag, lambda node: calls.append(node.node_id) or True)

    assert result.success
    assert result.completed == ("inspect", "research", "test")
    assert calls == ["inspect", "research", "test"]
