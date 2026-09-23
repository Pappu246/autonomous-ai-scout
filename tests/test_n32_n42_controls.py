from pathlib import Path

from autonomous_agent.budget import BudgetExceededError, BudgetLedger, ResourceBudget
from autonomous_agent.background_worker import BackgroundTaskWorker
from autonomous_agent.task_queue import TaskQueueStore
from autonomous_agent.concurrency import ExecutionLeaseStore
from autonomous_agent.delegation import DelegationError, build_delegation
from autonomous_agent.goal_loop import run_goal_loop
from autonomous_agent.resilience import RecoveryAction, classify_failure
from autonomous_agent.self_evaluation import evaluate_execution
from autonomous_agent.observability import TelemetryBuffer
from autonomous_agent.triggers import TriggerRegistry
from autonomous_agent.benchmark_harness import BenchmarkCase, run_benchmark
from autonomous_agent.readiness import ReadinessCheck, evaluate_readiness
from autonomous_agent.production_audit import AuditFinding, run_production_audit


def test_n32_lease_prevents_duplicate_resource(tmp_path: Path):
    store = ExecutionLeaseStore(tmp_path / "leases.json", max_active=2, ttl_seconds=30)
    first = store.acquire("task:1", "worker-a")
    assert first is not None
    assert store.acquire("task:1", "worker-b") is None
    assert store.release(first.lease_id)
    assert store.acquire("task:1", "worker-b") is not None


def test_n32_worker_uses_durable_lease(tmp_path: Path):
    queue = TaskQueueStore(tmp_path / "queue.json")
    queue.enqueue("do work", task_id="task-1", execution_id="exec-1")
    leases = ExecutionLeaseStore(tmp_path / "leases.json", max_active=1)
    seen = []
    worker = BackgroundTaskWorker(queue, lambda item: seen.append(item.task_id) or True, lease_store=leases)
    result = worker.run_once()
    assert result is not None
    assert result.state.value == "succeeded"
    assert seen == ["task-1"]
    assert leases.active() == ()


def test_n33_delegation_is_scope_bounded():
    plan = build_delegation(
        "parent",
        [("inspect repo", ["inspect"]), ("read files", ["read_file"])],
        parent_capabilities=["inspect", "read_file"],
        parent_approved=False,
    )
    assert len(plan.children) == 2
    assert plan.children[0].parent_task_id == "parent"
    try:
        build_delegation(
            "parent",
            [("send email", ["email.send"])],
            parent_capabilities=["email.send"],
            parent_approved=False,
        )
    except DelegationError:
        pass
    else:
        raise AssertionError("unapproved write-capable delegation must be rejected")


def test_n34_self_evaluation_requires_verified_results():
    passed = evaluate_execution(
        expected_operations=["workspace_shell"],
        actual_operations=["workspace_shell"],
        verification_statuses=["verified"],
    )
    failed = evaluate_execution(
        expected_operations=["workspace_shell"],
        actual_operations=["workspace_shell"],
        verification_statuses=["pending"],
    )
    assert passed.passed
    assert not failed.passed


def test_n35_goal_loop_is_bounded():
    calls = {"count": 0}

    def observe(name: str) -> bool:
        calls["count"] += 1
        return calls["count"] >= 2

    result = run_goal_loop("verify goal", ["check"], observe, max_iterations=3)
    assert not result.stopped
    assert result.iterations == 2


def test_n36_budget_fails_closed():
    ledger = BudgetLedger(ResourceBudget(tool_calls=1))
    ledger.consume(tool_calls=1)
    try:
        ledger.consume(tool_calls=1)
    except BudgetExceededError:
        pass
    else:
        raise AssertionError("budget overrun must fail closed")


def test_n37_unknown_external_outcome_requires_reconciliation():
    directive = classify_failure(
        exception_type="TimeoutError",
        attempt=1,
        max_attempts=3,
        side_effect_state="unknown",
    )
    assert directive.action is RecoveryAction.RECONCILE


def test_n38_telemetry_redacts_secrets():
    telemetry = TelemetryBuffer(max_events=2)
    telemetry.record("call", value="API_KEY=super-secret")
    assert "super-secret" not in str(telemetry.snapshot())
    assert len(telemetry.fingerprint()) == 64


def test_n39_trigger_cooldown_deduplicates():
    registry = TriggerRegistry()
    trigger = registry.register("github.ci", {"sha": "abc"}, cooldown_seconds=60)
    assert registry.fire(trigger.trigger_id)
    assert not registry.fire(trigger.trigger_id)


def test_n39_trigger_registry_persists(tmp_path: Path):
    path = tmp_path / "triggers.json"
    first = TriggerRegistry(path=path)
    trigger = first.register("github.ci", {"sha": "abc"}, cooldown_seconds=60)
    assert first.fire(trigger.trigger_id)
    second = TriggerRegistry(path=path)
    assert not second.fire(trigger.trigger_id)


def test_n40_benchmark_harness_is_deterministic():
    report = run_benchmark(
        [BenchmarkCase("one", 1, 2), BenchmarkCase("two", 2, 4)],
        lambda value: value * 2,
        version="test-1",
    )
    assert report.score == 1.0
    assert all(item.error_type is None for item in report.observations)


def test_n41_readiness_requires_all_checks():
    ready = evaluate_readiness([ReadinessCheck("tests", True, "699 passed"), ReadinessCheck("ci", True, "green")])
    blocked = evaluate_readiness([ReadinessCheck("tests", True, "green"), ReadinessCheck("ci", False, "failed")])
    assert ready.ready
    assert not blocked.ready


def test_n42_production_audit_requires_required_areas():
    areas = [
        "concurrency", "replay", "queue_bounds", "prompt_injection",
        "secret_redaction", "shell_safety", "approval", "ci", "external_data",
    ]
    passed = run_production_audit([AuditFinding(area, True, "verified") for area in areas])
    missing = run_production_audit([AuditFinding("ci", True, "verified")])
    assert passed.passed
    assert not missing.passed
