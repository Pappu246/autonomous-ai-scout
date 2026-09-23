from __future__ import annotations

import tempfile
from pathlib import Path

from autonomous_agent.benchmark_harness import BenchmarkCase, run_benchmark
from autonomous_agent.budget import BudgetLedger, ResourceBudget
from autonomous_agent.capability_policy import Capability
from autonomous_agent.execution_engine import ExecutionState, execute_plan
from autonomous_agent.task_planner import plan_task
from autonomous_agent.task_queue import TaskQueueStore
from autonomous_agent.trigger_dispatch import TriggerQueueDispatcher
from autonomous_agent.concurrency import ExecutionLeaseStore
from autonomous_agent.delegation import build_delegation
from autonomous_agent.external_side_effects import ExternalSideEffectStore, canonical_request_digest
from autonomous_agent.goal_loop import run_goal_loop
from autonomous_agent.observability import TelemetryBuffer
from autonomous_agent.production_audit import AuditFinding, run_production_audit
from autonomous_agent.readiness import ReadinessCheck, evaluate_readiness
from autonomous_agent.resilience import RecoveryAction, classify_failure
from autonomous_agent.self_evaluation import evaluate_execution
from autonomous_agent.triggers import TriggerRegistry


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="n31-n42-") as directory:
        root = Path(directory)

        side = ExternalSideEffectStore(root / "effects.json")
        digest = canonical_request_digest("email.send", {"to": "demo@example.com", "body": "hello"})
        assert side.claim(key="demo-send", operation="email.send", request_digest=digest).allowed
        side.mark_executed("demo-send", result_digest="a" * 64)
        assert not side.claim(key="demo-send", operation="email.send", request_digest=digest).allowed

        leases = ExecutionLeaseStore(root / "leases.json", max_active=2, ttl_seconds=30)
        lease = leases.acquire("demo-task", "demo-worker")
        assert lease is not None
        assert leases.acquire("demo-task", "other-worker") is None
        leases.release(lease.lease_id)

        delegation = build_delegation(
            "root",
            [("inspect", ["inspect"]), ("read", ["read_file"])],
            parent_capabilities=["inspect", "read_file"],
            parent_approved=False,
        )
        assert len(delegation.children) == 2

        evaluation = evaluate_execution(
            expected_operations=["inspect"],
            actual_operations=["inspect"],
            verification_statuses=["verified"],
        )
        assert evaluation.passed

        goal = run_goal_loop("demo goal", ["check"], lambda _: True, max_iterations=2)
        assert not goal.stopped

        budget = BudgetLedger(ResourceBudget(tool_calls=2))
        budget.consume(tool_calls=1)
        assert budget.usage.tool_calls == 1

        recovery = classify_failure(
            exception_type="TimeoutError",
            attempt=1,
            max_attempts=3,
            side_effect_state="unknown",
        )
        assert recovery.action is RecoveryAction.RECONCILE

        telemetry = TelemetryBuffer()
        telemetry.record("demo", credential="API_KEY=hidden")
        assert "hidden" not in str(telemetry.snapshot())

        triggers = TriggerRegistry(path=root / "triggers.json")
        trigger = triggers.register("demo.event", {"id": "1"}, cooldown_seconds=60)
        assert triggers.fire(trigger.trigger_id)

        dispatch_triggers = TriggerRegistry(path=root / "dispatch-triggers.json")
        dispatch_trigger = dispatch_triggers.register("demo.queue", {"id": "2"}, cooldown_seconds=60)
        dispatcher = TriggerQueueDispatcher(dispatch_triggers, TaskQueueStore(root / "queue.json"))
        dispatched = dispatcher.dispatch(
            dispatch_trigger.trigger_id,
            task="process demo event",
            task_id="demo-event-2",
            execution_id="demo-execution-2",
        )
        assert dispatched.queued

        plan = plan_task("inspect repository", granted=[Capability.INSPECT])
        assert plan.executable
        canonical_budget = BudgetLedger(ResourceBudget(tool_calls=2, attempts=2, output_bytes=64 * 1024))
        canonical_telemetry = TelemetryBuffer(max_events=16)
        canonical = execute_plan(
            plan,
            root,
            granted=[Capability.INSPECT],
            audit_path=root / "canonical.jsonl",
            execution_id="demo-canonical",
            budget_ledger=canonical_budget,
            telemetry=canonical_telemetry,
        )
        assert canonical.state is ExecutionState.VERIFIED
        assert any(event["name"] == "tool_result" for event in canonical_telemetry.snapshot())

        benchmark = run_benchmark([BenchmarkCase("double", 2, 4)], lambda value: value * 2)
        assert benchmark.score == 1.0

        readiness = evaluate_readiness(
            [ReadinessCheck("tests", True, "local"),
             ReadinessCheck("policy", True, "local")]
        )
        assert readiness.ready

        areas = (
            "concurrency", "replay", "queue_bounds", "prompt_injection",
            "secret_redaction", "shell_safety", "approval", "ci", "external_data",
        )
        audit = run_production_audit([AuditFinding(area, True, "demo") for area in areas])
        assert audit.passed

    print("N31-N45 validation demo: VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
