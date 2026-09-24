# N31 — Durable External Side-Effect Protection

## Objective

Prevent an approved external write from being replayed automatically after a process restart, timeout, or uncertain remote outcome.

N31 introduces a durable side-effect ledger with a fail-closed lifecycle:

`RESERVED → EXECUTED`
`RESERVED → UNKNOWN`
`RESERVED → FAILED`

A `RESERVED` or `UNKNOWN` record is never replayed automatically because the remote system may already have applied the side effect. Operators must reconcile the external system before any new execution is authorized.

## Identity

Each side effect has:

- a bounded operation name;
- a stable side-effect key;
- a deterministic request digest;
- a durable state;
- bounded execution/result metadata.

The approval flag itself is excluded from request identity so changing approval metadata cannot create a second external action.

## Durability

Ledger writes use:

- temporary-file serialization;
- flush + fsync;
- atomic replace;
- schema and duplicate-key validation;
- bounded record count and field sizes.

A corrupted ledger fails closed rather than being treated as empty.

## Acceptance

N31 is considered implemented at the ledger boundary when:

1. a first claim is persisted;
2. a later process sees the same key and refuses duplicate execution;
3. an unresolved prior attempt blocks automatic replay;
4. key reuse with different request contents fails closed;
5. failed/unknown external outcomes are terminal for automatic retry;
6. atomic persistence and restart loading are covered by tests.

## Limitation

The ledger cannot prove physical exactly-once execution against an arbitrary third-party provider by itself. Provider-supported idempotency semantics are still required for the strongest end-to-end guarantee. N31 therefore treats uncertain outcomes as non-replayable and requires reconciliation instead of assuming that a remote timeout means "nothing happened".

## Next boundaries

N32 is intentionally separate: concurrent execution and multi-worker coordination are not part of this phase.
