# N46 Canonical Admission Gate

N46 turns the N41 readiness evidence and N42 production audit primitives into a fail-closed gate at the canonical execution boundary.

## Contract

The gate binds a non-secret admission request to:

- the exact canonical task digest;
- the exact authorization digest;
- current readiness evidence;
- current production-audit evidence; and
- explicit approval whenever the execution is marked as side-effecting.

A missing, invalid, stale, or failed input blocks execution. The gate does not execute work and does not grant new capabilities.

## Digest binding

The admission decision includes a deterministic digest over the request and the non-secret evidence summary. Raw evidence strings are intentionally excluded from that digest so free-form operator text cannot become a hidden credential or payload channel.

## Canonical integration

`autonomous_agent.execution_engine.execute_plan(...)` accepts an optional `AdmissionRequest` together with a `ReadinessReport` and `ProductionAudit`. When supplied, all three are required and are checked before any tool attempt starts.

The existing consequence-aware approval policy and registry authorization checks remain authoritative. N46 adds a higher-level production admission gate; it does not replace those controls.

## Fail-closed cases

Execution is blocked when any of these conditions occur:

- malformed admission identity or digest;
- task or authorization digest mismatch;
- readiness is false;
- production audit is false;
- side effects are requested without explicit approval; or
- any required admission input is missing.

## Non-goals

N46 does not auto-approve, auto-merge, deploy, widen permissions, or bypass the existing sandbox, checkpoint, budget, telemetry, or external-side-effect controls.
