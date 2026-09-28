# N31-N46 — Bounded Autonomy Controls

This extension adds bounded control primitives on top of the existing N9-N30, Phase 6, Phase 7, Phase 8 and Phase 9 boundaries. It does not add a second executor, authorization engine, sandbox, lifecycle authority or deployment path.

## N31 — Durable External Side-Effect Protection
External write connectors use a durable ledger with RESERVED, EXECUTED, UNKNOWN and FAILED states. RESERVED and UNKNOWN outcomes are not automatically replayed. Gmail, Calendar and bounded REST write paths can bind to the ledger.

## N32 — Durable Execution Leases
The background worker can acquire a durable execution lease for one logical task. Duplicate ownership is refused and lease state survives process restarts.

## N33 — Capability-Scoped Delegation
Delegated child work is bounded by the parent's capabilities, approval state, depth and child-count limits. Delegation does not widen authority.

## N34 — Deterministic Self-Evaluation
Execution evaluation compares expected operations with actual operations and verification evidence. Unverified outcomes cannot become success.

## N35 — Bounded Goal Loops
Longer goals use an explicit iteration ceiling. Observation does not grant new permissions.

## N36 — Resource Budgets
ResourceBudget and BudgetLedger bound attempts, tool calls, wall-clock time, output bytes and external side effects. The canonical execute_plan path can enforce the ledger before and after each tool attempt.

## N37 — Recovery Classification
Failures are classified into bounded retry, reconciliation or abort. Unknown external outcomes require reconciliation rather than blind replay.

## N38 — Runtime Telemetry
TelemetryBuffer provides bounded, secret-redacted runtime events. Telemetry is evidence only and cannot grant authority.

## N39 — Persistent Triggers
Trigger fingerprints, cooldowns and persistent trigger state prevent duplicate trigger-driven work across restarts.

## N40 — Deterministic Benchmark Harness
BenchmarkCase and run_benchmark provide bounded deterministic evaluation evidence.

## N41 — Readiness Gate
Readiness is the aggregation of independent safety checks. It does not deploy or authorize work by itself.

## N42 — Production Safety Audit
ProductionAudit requires evidence for concurrency, replay, queue bounds, prompt-injection defense, secret redaction, shell safety, approval, CI and external-data handling.

## N43-N45 — Canonical Control Integration
The canonical execution engine accepts an optional BudgetLedger and TelemetryBuffer. TriggerQueueDispatcher hands approved trigger work to the existing durable TaskQueueStore; it never executes queued work itself.

## N46 — Canonical Admission Gate
AdmissionRequest binds exact task and authorization digests, execution identity, side-effect classification, explicit approval state, readiness and production-audit evidence. Any mismatch or missing evidence blocks execution before tool attempts begin.

## Safety boundary
No unrestricted shell, arbitrary outbound network, autonomous deployment, autonomous billing, destructive action or unrestricted self-modification is introduced by N31-N46.

## Validation
Final current-main integration branch validation remains CI-gated. The branch is kept separate from production main until the full regression suite and integration review are green.
