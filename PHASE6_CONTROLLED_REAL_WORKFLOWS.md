# Phase 6 — Controlled Real Workflow Execution

Status: **DESIGN / SCOPE LOCK**

Phase 5 established bounded cross-domain workflow orchestration, deterministic mock execution, fail-closed unsupported execution, post-condition observation, replay protection, checkpoint/resume, approval gates, and auditability.

Phase 6 extends that foundation with a **controlled real-workflow execution backend**. It does not remove Phase 5 gates and does not introduce unrestricted network or autonomous delivery.

## 1. Objective

Enable selected Phase 5 workflow operations to execute against already-registered capability domains through explicit, bounded adapters while preserving:

```
goal
  → plan
  → authorization
  → capability routing
  → sandbox
  → workflow connector
  → controlled backend
  → independent observation
  → verification
  → audit
```

The key change is:

```
Mock / Unsupported backend
        ↓
Controlled real backend adapters
```

The backend is an execution implementation, **not an authority source**.

## 2. Non-goals

Phase 6 does **not**:

- grant unrestricted machine access;
- create a generic shell, Python, JavaScript, browser-debugger, or process-execution escape hatch;
- permit arbitrary outbound network connections;
- autonomously send email/messages;
- autonomously publish content;
- bypass human approval for controlled writes;
- treat a provider response as proof of success;
- store raw credentials or secrets in workflow state/checkpoints;
- widen a capability grant because a workflow requests it;
- introduce a second planner/runtime/authorization path;
- start Phase 7 functionality.

## 3. Architecture

```
                 ┌─────────────────────────────┐
User Goal ──────►│ Existing planner / policy   │
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │ Capability authorization    │
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │ Existing sandbox boundary   │
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │ BoundedWorkflowConnector     │
                 │ Phase 5 enforcement point    │
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │ ControlledRealWorkflowBackend│
                 └──────────────┬──────────────┘
                                ▼
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
  existing web            existing filesystem      existing communication
  / browser / docs        / application adapters   adapters
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │ Independent observer        │
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │ Digest + evidence verify   │
                 │ + audit + checkpoint        │
                 └─────────────────────────────┘
```

There is **one** planner, **one** authorization boundary, **one** sandbox boundary and **one** workflow enforcement point.

## 4. Phase 6 execution contract

Every real backend operation MUST expose:

1. deterministic input validation;
2. capability identity;
3. minimum required scope;
4. provider/connector identity;
5. network policy;
6. approval requirement;
7. idempotency key;
8. precondition;
9. provider result;
10. independent observation evidence;
11. output digest;
12. audit record.

Backend acceptance alone never yields `VERIFIED`.

A real operation can end only in:

- `FAILED`
- `BLOCKED`
- `ACCEPTED`
- `OBSERVED`
- `VERIFIED`
- `RECOVERY_REQUIRED`

depending on the existing workflow state machine.

## 5. Capability boundaries

### Read-only operations

Phase 6 first targets read-only/observation operations where existing adapters already provide bounded access.

Examples:

- web research / source extraction;
- browser open/extract within the existing bounded browser transport;
- filesystem read/list within the bound workspace;
- repository metadata/read operations;
- calendar/email read operations already exposed as read-only.

### Controlled writes

Write operations remain approval-gated and must go through the existing Phase 5 policy.

Examples:

- create/update a bounded workspace artifact;
- create a calendar event;
- prepare a communication draft.

The implementation must keep **draft != send**.

### Explicitly excluded from autonomous Phase 6

- email/message sending;
- arbitrary public posting;
- unrestricted browser debugging or CDP;
- OS process spawning;
- shell execution;
- credential exfiltration;
- arbitrary URL fetch outside an approved provider/adapter boundary.

## 6. Milestones

### M1 — Contract and adapter interfaces

Deliver:

- `ControlledRealWorkflowBackend` contract;
- provider operation descriptor;
- execution envelope;
- idempotency envelope;
- observation envelope;
- fail-closed behavior for unsupported operations;
- unit tests for schema, authorization and boundary invariants.

Exit criteria:

- no existing Phase 5 behavior changes;
- unsupported operations still fail closed;
- no new network escape path.

### M2 — Read-only real execution vertical slice

Implement one end-to-end bounded read-only workflow using an already-existing capability adapter.

Required path:

```
workflow request
→ authorization
→ adapter
→ provider result
→ independent observation
→ digest re-derivation
→ VERIFIED / non-verified terminal state
```

Exit criteria:

- real provider result is never trusted as verification;
- artifact digests are independently recomputed;
- provenance is preserved;
- replay of a verified read step is refused or safely deduplicated.

### M3 — Controlled write vertical slice

Implement exactly one approval-gated write path using an existing adapter.

Required properties:

- explicit human approval;
- idempotency key;
- precondition check;
- mutation result observation;
- postcondition verification;
- no automatic retry after ambiguous mutation without a safe recovery path.

Exit criteria:

- missing approval blocks;
- stale/replayed requests block;
- ambiguous mutation enters `RECOVERY_REQUIRED`;
- no autonomous send/delivery.

### M4 — Durable recovery and idempotency hardening

Add adversarial coverage for:

- duplicate requests;
- repeated provider responses;
- stale workflow epochs;
- mismatched output digests;
- partial completion;
- provider timeout after mutation;
- checkpoint/resume;
- approval substitution;
- capability/scope narrowing;
- cross-domain trust regression.

Exit criteria:

- no silent duplicate mutation;
- no trust escalation;
- recovery never replays a verified side effect.

### M5 — Security review and evaluation

Run:

```
pytest -q
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
pytest -q tests/test_workflow_safety.py tests/test_communication_safety.py
python3 -m compileall autonomous_agent tests
```

Plus Phase 6:

- adapter contract tests;
- provider boundary tests;
- replay/idempotency tests;
- approval bypass tests;
- prompt-injection tests at data→control boundaries;
- static scan for process/shell/raw-network/secret-storage paths.

Document actual results. No fabricated success rate.

### M6 — Release gate

Before merge:

- compare Phase 5 baseline vs Phase 6;
- verify no Phase 5 regression;
- verify capability catalog changes are explicit;
- verify all newly reachable operations have sandbox + audit coverage;
- verify `email.send` remains non-autonomous;
- verify no Phase 7 functionality exists;
- merge only after CI is green.

## 7. Security invariants

These are release-blocking invariants:

1. **Authority comes from runtime authorization, never from workflow data.**
2. **Trust only flows downward.**
3. **Capability grants only narrow; never widen.**
4. **External/untrusted content cannot create approval.**
5. **Backend/provider claims are not verification evidence by themselves.**
6. **Every successful mutation must be independently observable.**
7. **Every artifact digest is re-derived from actual payload.**
8. **Mutation retry requires a safe idempotency/recovery decision.**
9. **Secrets are references only; raw secrets never enter workflow state or checkpoints.**
10. **No generic execution primitive is exposed.**
11. **No unrestricted network path is introduced.**
12. **Draft creation is not delivery.**

## 8. Metrics and reporting

Phase 6 metrics must distinguish:

### Execution metrics

- requested;
- authorized;
- blocked;
- accepted;
- observed;
- verified;
- recovery required;
- failed.

### Safety metrics

- approval bypass attempts blocked;
- replay attempts blocked;
- trust-escalation attempts blocked;
- digest mismatches detected;
- unsupported operations fail-closed;
- secret-leakage test cases blocked.

### Reliability metrics

- duplicate mutation rate;
- verification failure rate;
- ambiguous-provider-result rate;
- recovery completion rate;
- median execution latency.

A successful run is **not** counted as verified unless the independent observer confirms the expected postcondition.

## 9. Relationship to Phase 5

Phase 5 remains the security/control foundation.

Phase 6 adds execution capability **under** that foundation:

```
Phase 5
  policy + trust + approval + replay + audit + observation
                    +
Phase 6
  controlled real adapters + idempotency + provider execution
                    =
bounded real workflows
```

Nothing in Phase 6 should weaken a Phase 5 invariant merely to make an example succeed.

## 10. Definition of done

Phase 6 is complete only when:

- at least one bounded read-only real workflow is independently verified;
- at least one controlled write is approval-gated and independently verified;
- ambiguous mutations recover without silent replay;
- adversarial tests cover the new execution boundary;
- existing Phase 5 tests remain green;
- static security review is clean;
- capability/network/sandbox/audit catalog is updated;
- production behavior remains explicit and documented;
- all measured results are reproducible and honestly reported.

**Phase 6 is not a goal of “maximum autonomy.” The goal is bounded, observable, reversible real execution.**
