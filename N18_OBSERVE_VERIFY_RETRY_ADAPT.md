# N18 — Observe → Verify → Retry → Adapt

## Objective

Add a bounded recovery controller that does more than repeat the same failed action: it observes the verified execution result, retries within a hard budget, and can replace the failed step through an injected replanner.

## Execution loop

```text
STEP
  │
  ▼
EXECUTE through existing sandbox/execution boundary
  │
  ▼
OBSERVE safe result metadata
  │
  ├── verified ──► VERIFICATION ──► STEP_COMPLETED
  │
  └── failed
       │
       ├── retry budget remaining ──► RETRY
       │                              │
       │                              └── execute again
       │
       └── retry exhausted
                │
                ▼
             REPLAN
                │
                ▼
          replacement step(s)
                │
                └──────────────► EXECUTE
```

## Implementation

`autonomous_agent/adaptive_execution.py` provides:

- `StepObservation` containing only safe result metadata;
- `AdaptiveExecutionResult` with observations, attempts, replans and final state;
- bounded retry and replan budgets;
- reuse of the existing `execute_plan` authorization, sandbox, audit and verification boundary;
- fail-closed behavior for blocked/recovery-required child executions;
- rejection of colliding replacement step identifiers.

`AutonomousTaskCore.execute_adaptive()` exposes this loop without creating a second task-planning boundary.

## Capability proof

Runnable demonstration:

```bash
python examples/n18_adaptive_execution_demo.py
```

The demo simulates a tool failure twice, observes the failure, replans from web research to browser navigation, then continues and reaches a verified result.

Expected capability trace:

```text
web.search  -> failed
web.search  -> failed
REPLAN      -> browser.open
browser.open -> verified
remaining planned steps -> verified
TASK        -> verified
```

This is an offline deterministic simulation; it makes no real network or external-service calls.

## Safety

- Retry and replan counts are hard-bounded.
- Existing capability grants are not widened by a replan.
- Approval gates remain enforced by the existing executor.
- A policy-blocked or recovery-required child execution is not handed to the replanner.
- Raw tool output is not copied into `StepObservation` or adaptive audit records.
- Existing audit integrity must verify before the loop begins.

## Tests

Coverage includes successful observe/retry/replan/verify behavior, proof that raw simulated output is not persisted in the adaptive audit, policy-blocked steps not being replanned, and explicit failure when recovery has no replanner.

## Limitations after N18

Still deferred:

- long-horizon multi-step task DAG planning (N19);
- durable reconstruction of an adaptive replanned trajectory across process restarts (N19/N31);
- background execution and queues (N20);
- universal digital-tool discovery beyond registered tools (N21+).

## Acceptance gate

N18 is VERIFIED only when focused recovery tests and full CI are green, the offline capability demo path is present, policy-blocked actions cannot be bypassed by replanning, and the merge-head CI is green.
