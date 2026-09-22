# N19 — Long-Horizon Task DAG

## Objective

Represent a high-level workflow as a bounded directed acyclic graph of canonical task plans, validate dependencies before execution, and expose a deterministic ready frontier.

## Capability

N19 adds a dependency layer above the canonical task planner:

```text
high-level objective
      │
      ▼
 DAGTaskSpec nodes
      │
      ▼
LongHorizonPlanner
      │
      ├── canonical plan per node
      ├── dependency validation
      ├── cycle detection
      └── deterministic DAG digest
      │
      ▼
topological / ready frontier
      │
      ▼
bounded DAG execution helper
```

Example graph:

```text
        ┌────────────┐
        │  inspect   │
        └─────┬──────┘
              │
              ▼
        ┌────────────┐
        │    test    │
        └────────────┘

        ┌────────────┐
        │  research  │
        └────────────┘
        (independent)
```

`inspect` and `research` are initially ready; `test` becomes ready only after `inspect` succeeds.

## Implementation

`autonomous_agent/task_dag.py` provides:

- `DAGTaskSpec` for task nodes and dependencies;
- `DAGNode` with its canonical `TaskPlan`;
- `TaskDAGPlan` with deterministic digest, topological order and ready frontier;
- bounded maximum node count;
- fail-closed missing dependency and cycle detection;
- `execute_dag()` that stops at a failed node and blocks dependent work.

`AutonomousTaskCore.prepare_dag()` exposes the planner through the unified task core.

## Capability proof

Runnable test:

```bash
pytest -q tests/test_task_dag.py
```

The current behavior is deterministic and sequential. Independent ready nodes are represented, but they are not run concurrently; concurrency is intentionally deferred to N32.

## Safety

- Each DAG node still uses the existing canonical task planner and Tool Registry.
- A non-executable node makes the DAG non-executable.
- Missing dependencies and cycles are rejected before execution.
- DAG execution does not bypass per-node capability, approval, sandbox or audit boundaries.
- Node count is bounded to prevent unbounded plan expansion.

## Limitations after N19

Still deferred:

- background worker/queue persistence (N20);
- concurrent execution of independent nodes (N32);
- model-assisted open-ended decomposition of arbitrary objectives;
- durable reconstruction of adaptive replans across restart (N31 and later).

## Acceptance gate

N19 is VERIFIED only when DAG planning tests, dependency-failure tests, full CI, and the runnable capability demo are green.
