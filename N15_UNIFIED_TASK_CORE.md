# N15 — Unified Autonomous Task Core

## Objective

Create one canonical entry point for task-driven agent work without adding a second planner, executor, lifecycle, or safety system.

The canonical path is:

```text
task request
    ↓
AutonomousTaskCore
    ↓
TaskOrchestrator
    ↓
task_planner / intent / decomposition / risk
    ↓
ToolRegistry + capability policy
    ↓
existing execution_engine + sandbox
```

## Implemented

- Added `autonomous_agent.task_core.AutonomousTaskCore`.
- Added `CanonicalTask` to carry the normalized request, canonical plan, deterministic task digest, and effective capability grants.
- Centralized runtime task planning and execution through `AutonomousTaskCore`.
- Added canonical planner helpers for selected tool names and safe capability grants.
- Kept `runtime._plan_for_request()` as a compatibility adapter that delegates to `AutonomousTaskCore`; it no longer contains its own keyword-routing logic.
- Preserved the existing task planner, tool registry, capability policy, sandbox, audit, lifecycle, and execution boundaries.
- Kept historical `task_engine.py` behavior as a compatibility surface; N15 does not delete unrelated legacy interfaces.

## Safety boundary

Default capability grants are derived only from registered tools marked `safe_autonomous`. Approval-gated tools do not receive an implicit write capability.

This means N15 does not weaken the existing approval model.

## Non-goals

N15 does not claim to provide:

- durable task persistence or resume,
- dynamic tool selection from model output,
- observe/replan/retry loops,
- background workers,
- parallel execution,
- computer/OS control beyond existing connectors.

Those capabilities remain explicitly assigned to later phases.

## Acceptance gate

N15 is complete only when all of the following are demonstrated:

1. The runtime task path enters through `AutonomousTaskCore`.
2. The same task produces a stable task digest and plan digest.
3. Safe tasks receive only safe-autonomous capability grants.
4. Approval-gated source changes remain blocked without approval.
5. Execution continues through the existing `execution_engine` boundary.
6. Compatibility callers delegate rather than implement a second planning path.
7. The full test suite passes in CI.
8. No N9–N14 regression is introduced.

## Evidence to retain

For the phase record, retain:

- CI run associated with the N15 pull request.
- Focused `tests/test_task_core.py` results.
- Final diff summary showing no changes to N9–N14 lifecycle semantics.
- Any real smoke-test result performed against an operator-provided target workspace.

A missing real smoke test is reported as `NOT_RUN`, not silently counted as a pass.
