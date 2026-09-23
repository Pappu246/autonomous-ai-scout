# N31-N42 Autonomy Extension Boundaries

This branch extends the existing N9-N30 safety architecture without adding a second executor, authorization engine, sandbox, lifecycle authority, audit authority, or memory authority.

## N31 — Durable External Side-Effect Protection
Persistent side-effect claims prevent automatic replay after restart or uncertain provider outcomes. Gmail, Calendar, and REST write connectors can use the same durable ledger.

## N32 — Concurrency Coordination
Durable execution leases bound simultaneous work and prevent duplicate ownership of the same logical task. Existing background workers remain the executor.

## N33 — Capability-Scoped Delegation
Delegated child tasks inherit a strict capability subset. Depth, child count, and write approval are bounded. Delegation never grants authority that the parent lacks.

## N34 — Deterministic Self-Evaluation
Execution outcomes are checked against expected operations and verification evidence. A model or caller cannot turn an unverified result into success.

## N35 — Bounded Goal Loop
Longer goals are represented as explicit bounded steps with a hard iteration ceiling. Observation does not itself grant new permissions.

## N36 — Resource Budgets
Attempts, tool calls, wall-clock time, output bytes, and external side effects have explicit ceilings. Exceeding a ceiling raises a fail-closed budget error.

## N37 — Recovery Classification
Failures are classified as bounded retry, reconciliation, or abort. Unknown external side effects always require reconciliation rather than blind replay.

## N38 — Observable Runtime Telemetry
Telemetry is bounded and secret-redacted. Events are evidence only and do not grant authority.

## N39 — Event Triggers
Triggers are content-fingerprinted and cooldown-bounded to prevent noisy duplicate work. Trigger firing still enters the existing task/approval/execution path.

## N40 — Deterministic Benchmark Harness
Benchmark cases are bounded, deterministic, and store pass/fail and latency evidence without enabling paid or uncontrolled provider retries.

## N41 — Readiness Gate
Release readiness is an explicit aggregation of independent evidence. A readiness result never deploys anything.

## N42 — Production Safety Audit
The final audit requires evidence for concurrency, replay safety, queue bounds, prompt-injection defense, secret redaction, shell safety, approval, CI, and malformed external data.

N42 is a gate and evidence model, not an unrestricted self-modification mechanism.
