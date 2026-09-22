# N27 — Context Management + Long-Term Task Memory

## Objective

As task histories grow, build a bounded working context from the live task, plan, observations and relevant N26 memory without replaying the entire history.

## Architecture

```text
current task
    │
    ├── plan
    ├── observations
    ├── pinned safety/context
    └── N26 memory recall
             │
             ▼
       ContextManager
             │
             ├── deduplicate
             ├── prioritize
             ├── enforce item/character limits
             └── generate stable digest
             │
             ▼
        ContextPacket
```

## Capability proof

Runnable demo/test:

```bash
python examples/n27_context_demo.py
pytest -q tests/test_context_manager.py
```

The demo records a prior task in N26 memory, reopens the store, then builds a bounded context for a new task and reports the recalled memory plus context digest.

## Semantics

Context is ephemeral working state. N27 does not create a new persistence backend and does not automatically persist the final assembled prompt.

The bounded retrieval score remains the deterministic N26 scorer; N27 decides what fits into the working context budget.

## Safety

- Current task content is normalized but not written by the context manager.
- Memory content comes from the redaction-protected N26 store.
- Maximum context size is bounded to 24,000 characters and 32 items.
- Duplicate context entries are removed before selection.
- Low-priority items are dropped when the budget is exhausted.

## Limitations after N27

Still deferred:

- authenticated credential broker (N28);
- prompt-injection isolation and memory poisoning defense (N29);
- consequence-aware approval policy (N30);
- cross-task transaction/idempotency integration (N31).

## Acceptance gate

N27 is VERIFIED when context assembly, memory recall, deterministic digest and budget enforcement pass full CI and the capability demo is available.
