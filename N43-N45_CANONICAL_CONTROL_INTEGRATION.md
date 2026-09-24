# N43-N45 Canonical Control Integration

These phases close the first integration gaps after the N31-N42 control primitives were introduced. The existing task lifecycle, authorization, sandbox, audit, queue, and worker remain authoritative.

## N43 — Canonical Resource Budget Enforcement

The canonical execute_plan path now accepts an optional BudgetLedger.

The ledger bounds:
- attempts
- tool calls
- external side effects for write-capable tools
- incremental wall-clock time
- emitted result bytes

Budget exhaustion fails the execution closed before a new tool attempt or after an already-completed tool result when the result itself exhausts an output/time ceiling. It never grants authority or retries automatically.

## N44 — Canonical Runtime Telemetry

The canonical execute_plan path now accepts an optional TelemetryBuffer.

Execution-start and tool-result events are recorded through the bounded, secret-redacted telemetry layer. Telemetry is observational only and cannot authorize, resume, or replay work.

## N45 — Trigger → Durable Queue Handoff

TriggerQueueDispatcher connects a cooldown-bounded TriggerRegistry to the existing durable TaskQueueStore.

The dispatcher:
1. checks trigger readiness;
2. creates or reuses a deterministic queue item;
3. records the trigger cooldown only after the queue handoff succeeds;
4. never executes the queued task itself.

A restart can encounter an already-created deterministic queue item without creating a second queue entry. Actual execution still belongs to the existing background worker and canonical execution path.

## Verification boundary

The new controls are covered by focused regression tests. Complete acceptance still requires the Windows full suite and the available CI evidence to remain green after these commits.
