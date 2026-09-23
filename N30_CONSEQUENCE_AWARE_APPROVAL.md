# N30 — Consequence-Aware Approval Policy

## Objective

Move approval decisions from brittle tool-name rules toward consequence metadata: risk, side effect type, approval requirement, high-impact capability, audit requirement, and untrusted origin.

## Capability proof

Runnable test/demo:

```bash
python examples/n30_consequence_policy_demo.py
pytest -q tests/test_consequence_policy.py
```

Expected behavior:

- `filesystem.read` is autonomous;
- `filesystem.write` requires explicit approval;
- deployment/secrets/billing/destructive actions require approval;
- untrusted-origin writes cannot silently become autonomous.

## Safety

The consequence policy is enforced both by the universal digital-tool invocation layer and by the canonical execution engine. It does not replace the Tool Registry: registry capability checks, sandbox requirements, audits and approval requirements remain authoritative. Safe read-only medium-risk operations remain autonomous, while writes/high-risk/critical operations remain approval-gated.

## Limitations after N30

Still deferred:

- exactly-once/idempotent external side effects (N31);
- concurrent execution (N32);
- multi-agent delegation (N33);
- self-evaluation (N34);
- budget enforcement (N36);
- full end-to-end benchmark (N40).

## Acceptance gate

N30 is VERIFIED when consequence-policy tests, full CI, and the capability demo are green and high-impact operations cannot become autonomous merely because a caller requests them.
