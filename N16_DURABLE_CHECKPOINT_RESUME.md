# N16 — Durable Checkpoint & Resume

## Objective

Persist enough non-secret execution state to resume an interrupted task without replaying steps whose verified completion has already been durably recorded.

## Implementation

N16 adds:

- ExecutionCheckpointStore with atomic JSON replacement.
- execution/task/plan identity binding.
- completed step tracking and cumulative attempt count.
- runtime checkpoint paths under state/runtime_checkpoints/<execution_id>.json.
- resume logic in the existing execution engine.
- terminal verified checkpoints that prevent duplicate execution for the same execution identity.

The checkpoint contains only execution metadata, digests, step identifiers, state, counts, and timestamps. Raw task text and credentials are not persisted.

## Resume boundary

```text
TASK
  │
  ▼
PLAN + TASK/PLAN DIGESTS
  │
  ▼
RUN STEP 1
  │
  ├── verified ──► CHECKPOINT(step-1)
  │
  ▼
RUN STEP 2
  │
  ├── process interruption
  │
  ▼
RESTART WITH SAME EXECUTION ID
  │
  ├── verify audit chain
  ├── load checkpoint
  ├── verify task + plan identity
  ├── skip step-1
  └── continue at step-2
```

A checkpoint is saved only after a step returns a successful, verified result. Therefore an interruption during the currently executing step may require that step to run again. N16 does not claim exactly-once external side effects; idempotency/transaction semantics are a later concern.

## Safety

- Invalid or unreadable checkpoints fail closed.
- A checkpoint for another execution identity is rejected.
- A checkpoint for a different task or plan is rejected.
- Existing sandbox, capability, audit, and approval checks remain in force.
- The pre-N16 behavior for an interrupted execution with no durable checkpoint remains recovery-required.
- No lifecycle replacement is introduced.

## Tests

Acceptance coverage includes:

1. simulated process interruption after the first verified step;
2. restart with the same execution identity;
3. proof that the completed first step is not replayed;
4. checkpoint contents contain no raw task text;
5. task/plan identity mismatch is blocked;
6. terminal verified checkpoint prevents duplicate execution.

## Capability proof

The concrete N16 capability is:

> Start a multi-step task, persist completion of step 1, terminate execution before step 2 finishes, restart with the same execution ID, and continue from step 2 without replaying step 1.

Runnable capability demo:

```bash
python examples/n16_checkpoint_demo.py
```

Expected trace:

```text
RUN #1
step-1 -> VERIFIED
checkpoint -> completed_step_ids=["step-1"]
step-2 -> INTERRUPTED

RUN #2
checkpoint loaded
step-1 -> SKIPPED
step-2 -> VERIFIED
checkpoint -> state="verified"
```

## Limitations after N16

Still intentionally deferred:

- dynamic tool selection and routing (N17);
- observe → verify → retry → adapt/replan loop (N18);
- long-horizon task DAG planning (N19);
- background worker/queue execution (N20);
- universal digital-tool layer (N21+);
- exactly-once external side-effect transactions and broader idempotency (N31).

## Acceptance gate

N16 is VERIFIED only when:

- implementation is merged to main;
- full CI is green at the exact merge head;
- interruption/resume test passes;
- identity mismatch is fail-closed;
- terminal duplicate execution is prevented;
- capability proof and limitation report are recorded.
