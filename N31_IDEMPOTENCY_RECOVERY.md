# N31 — Idempotency + Transactions + Recovery

## Objective

Prevent duplicate execution for the same side-effect key and make ambiguous crash states explicit instead of blindly replaying an external effect.

## Architecture

```text
tool invocation
     │
     ├── idempotency_key
     ▼
EffectLedger
     │
     ├── PREPARED ──► crash/ambiguous ──► RECOVERY_REQUIRED
     ├── COMMITTED ──► return saved safe result
     └── FAILED
     │
     ▼
IdempotentEffectRunner
```

## Capability proof

Runnable test/demo:

```bash
python examples/n31_idempotency_demo.py
pytest -q tests/test_idempotency.py
```

Repeated calls with the same key execute the injected side-effect only once. A pre-existing `PREPARED` state is not replayed automatically; it requires an explicit recovery result.

## Transaction boundary

N31 cannot make an arbitrary remote API exactly-once without a shared transaction/idempotency contract with that remote system. Therefore the agent uses an idempotency ledger and fails closed on ambiguous prepared state instead of claiming exactly-once semantics it cannot guarantee.

## Safety

- Effect keys are explicit and durable.
- Ledger snapshots are written atomically.
- Persisted effect results are bounded and common credential patterns are redacted.
- Duplicate keys do not invoke the external operation again after a committed record.
- Ambiguous `PREPARED` state requires explicit recovery.

## Limitations after N31

Still deferred:

- true concurrent scheduling (N32);
- multi-agent delegation (N33);
- resource/cost budgets (N36);
- complete observability timeline (N37).

## Acceptance gate

N31 is VERIFIED when idempotency, duplicate prevention, recovery-required semantics, safe result persistence and full CI are green.
