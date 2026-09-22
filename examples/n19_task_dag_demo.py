from autonomous_agent.capability_policy import Capability
from autonomous_agent.task_dag import DAGTaskSpec, LongHorizonPlanner, execute_dag


def main() -> int:
    dag = LongHorizonPlanner().plan(
        "verify a project",
        (
            DAGTaskSpec("inspect", "inspect repository"),
            DAGTaskSpec("research", "research this topic"),
            DAGTaskSpec("test", "run tests", ("inspect",)),
        ),
        granted=[Capability.INSPECT, Capability.TEST, Capability.WEB_RESEARCH],
    )
    print("N19 Task DAG capability demo")
    print(f"digest: {dag.digest}")
    print(f"ready now: {dag.ready_frontier}")
    print(f"topological order: {dag.topological_order()}")

    calls: list[str] = []
    result = execute_dag(dag, lambda node: calls.append(node.node_id) or True)
    print(f"execution order: {calls}")
    print(f"completed: {result.completed}")
    print(f"success: {result.success}")
    return 0 if result.success and result.completed == ("inspect", "research", "test") else 1


if __name__ == "__main__":
    raise SystemExit(main())
