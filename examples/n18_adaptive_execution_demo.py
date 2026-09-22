from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from autonomous_agent import execution_engine
from autonomous_agent.adaptive_execution import execute_adaptive_plan
from autonomous_agent.capability_policy import Capability
from autonomous_agent.sandbox import SandboxResult
from autonomous_agent.task_planner import plan_task


def main() -> int:
    plan = plan_task(
        "research this topic",
        granted=[Capability.WEB_RESEARCH, Capability.BROWSER],
    )
    calls: list[str] = []

    def simulated_tool(operation, *args, **kwargs):
        calls.append(operation)
        failed = len(calls) <= 2
        return SandboxResult(
            operation,
            not failed,
            1 if failed else 0,
            "simulated failure" if failed else "simulated verified result",
            False,
            (),
            "failed" if failed else "verified",
            "start",
            "finish",
            True,
        )

    original = execution_engine.run_safe_operation
    execution_engine.run_safe_operation = simulated_tool
    try:
        def replanner(observation, remaining):
            print(f"OBSERVE: {observation.tool_name} failed; adapting...")
            return plan_task(
                "browser navigation to https://example.com",
                granted=[Capability.BROWSER],
            ).steps[:1]

        with TemporaryDirectory(prefix="scout-n18-") as directory:
            audit = Path(directory) / "adaptive.jsonl"
            result = execute_adaptive_plan(
                plan,
                Path(directory),
                granted=[Capability.WEB_RESEARCH, Capability.BROWSER],
                audit_path=audit,
                execution_id="n18-demo",
                max_retries_per_step=1,
                max_replans=1,
                replanner=replanner,
            )
            print("N18 Observe → Verify → Retry → Adapt demo")
            print(f"RESULT: {result.state.value}")
            print(f"REPLANS: {result.replans}")
            print(f"TOOL TRACE: {calls}")
            print(
                "OBSERVATIONS: "
                + str([(item.tool_name, item.outcome) for item in result.observations])
            )
            return 0 if result.state.value == "verified" and result.replans == 1 else 1
    finally:
        execution_engine.run_safe_operation = original


if __name__ == "__main__":
    raise SystemExit(main())
