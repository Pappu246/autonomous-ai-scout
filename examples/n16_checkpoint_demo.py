from __future__ import annotations

import json
import tempfile
from pathlib import Path

from autonomous_agent import execution_engine
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_engine import ExecutionState, execute_plan
from autonomous_agent.sandbox import SandboxResult
from autonomous_agent.task_planner import plan_task


def verified_result(operation: str) -> SandboxResult:
    return SandboxResult(
        operation, True, 0, "demo", False, (), "verified",
        "start", "finish", True,
    )


def main() -> int:
    plan = plan_task("run the tests", granted=[Capability.INSPECT, Capability.TEST])
    if not plan.executable:
        raise RuntimeError(f"demo plan is not executable: {plan.reason}")

    with tempfile.TemporaryDirectory(prefix="scout-n16-") as directory:
        root = Path(directory)
        audit = root / "execution.jsonl"
        checkpoint = root / "checkpoint.json"
        execution_id = "n16-demo"
        calls: list[str] = []

        def interrupt_second(operation: str, *_args, **_kwargs) -> SandboxResult:
            calls.append(operation)
            if len(calls) == 2:
                raise KeyboardInterrupt("simulated process interruption")
            return verified_result(operation)

        original = execution_engine.run_safe_operation
        execution_engine.run_safe_operation = interrupt_second
        try:
            try:
                execute_plan(
                    plan, root, granted=[Capability.INSPECT, Capability.TEST],
                    audit_path=audit, checkpoint_path=checkpoint,
                    execution_id=execution_id,
                )
            except KeyboardInterrupt:
                print("RUN #1: interrupted during step-2")
        finally:
            execution_engine.run_safe_operation = original

        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        print(f"checkpoint after interruption: {saved['completed_step_ids']}")

        resumed_calls: list[str] = []

        def finish_remaining(operation: str, *_args, **_kwargs) -> SandboxResult:
            resumed_calls.append(operation)
            return verified_result(operation)

        execution_engine.run_safe_operation = finish_remaining
        try:
            result = execute_plan(
                plan, root, granted=[Capability.INSPECT, Capability.TEST],
                audit_path=audit, checkpoint_path=checkpoint,
                execution_id=execution_id,
            )
        finally:
            execution_engine.run_safe_operation = original

        final = json.loads(checkpoint.read_text(encoding="utf-8"))
        print(f"RUN #2: state={result.state.value}")
        print(f"resumed tool calls: {resumed_calls}")
        print(f"final checkpoint: {final['state']} / {final['completed_step_ids']}")
        if result.state is not ExecutionState.VERIFIED or resumed_calls != ["test"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
