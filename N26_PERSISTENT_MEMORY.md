# N26 — Persistent Episodic + Semantic Memory

## Objective

Persist task episodes and useful facts across process restarts, then retrieve relevant memories with deterministic bounded relevance scoring.

## Architecture

```text
task/result
    │
    ▼
PersistentMemory
    │
    ├── record_episode / record_fact
    ├── redaction + bounded fields
    └── CrossProjectMemory
              │
              ▼
       hash-chained durable log
              │
              ▼
      reopen / restart-safe load
              │
              ▼
       bounded relevance recall
```

N26 deliberately builds on `CrossProjectMemory`; it does not introduce a second persistence backend.

## Capability proof

Runnable demo/test:

```bash
python examples/n26_memory_demo.py
pytest -q tests/test_persistent_memory.py
```

The demonstration records a verified task episode, closes/reopens the memory store, retrieves it using a related query, and shows secret material is redacted before persistence.

## Semantics

`PersistentMemory.recall()` uses deterministic token-overlap cosine-style scoring over sanitized task/summary/fact fields. This is a lightweight semantic retrieval foundation; embedding/vector retrieval is not claimed yet.

## Safety

- Existing `CrossProjectMemory` redaction and hash-chain integrity remain the storage boundary.
- Task text, summaries, fact values and metadata are sanitized before persistence.
- Recall is bounded to at most 10 results by default.
- Malformed/corrupt memory remains fail-closed through the existing store.

## Limitations after N26

Still deferred:

- long-term context compaction and memory selection across large histories (N27);
- credential/permission memory and secret vaulting (N28);
- adversarial memory poisoning defenses (N29);
- transaction/idempotency integration with all external side effects (N31).

## Acceptance gate

N26 is VERIFIED when persistence/restart recall, relevance ranking, redaction, bounded recall, and task-core integration pass full CI and the runnable capability demo is available.
