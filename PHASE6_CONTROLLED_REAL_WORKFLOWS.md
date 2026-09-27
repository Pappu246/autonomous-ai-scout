# Phase 6 — Controlled Real Workflow Execution

Status: **M3 IMPLEMENTED — M4 PENDING**

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

### M1 implementation status

M1 is implemented in `autonomous_agent/workflow/real_backend.py`.
The contract includes an explicit `RealWorkflowAdapter`, provider operation descriptors,
secret-free execution envelopes, deterministic idempotency keys, provider-result and
observation envelopes, fail-closed default behavior, artifact bounds, provider/step
contract matching, and duplicate-execution protection.

CI validation on the M1 head reported **1989 passed, 6 skipped**.

M1 intentionally adds no concrete network/provider client. The real backend is only
live when an adapter is injected explicitly; the existing Phase 5 connector still
owns independent verification.

### M2 implementation status

M2 is implemented as a real, read-only vertical slice over the existing
filesystem:read capability:

- capability: filesystem:read
- existing connector: WorkspaceConnector
- Phase 6 adapter: WorkspaceRealWorkflowAdapter
- network policy: none
- effect: read_only
- approval: not required
- observation: the same bounded workspace file is re-read after provider acceptance
- verification: the Phase 5 connector independently re-derives the artifact digest and
  compares it with the second read
- secret handling: workspace content is redacted; credential-looking labels are
  neutralized again at the provider boundary
- path safety: the existing root-bound WorkspaceConnector blocks traversal and
  credential/VCS paths

The M2 tests exercise an actual temporary workspace rather than a mock backend.
The provider result is therefore real adapter execution over the bounded workspace,
while the existing observation/verification chain remains authoritative.

Latest CI validation: 1994 passed, 6 skipped.
### M3 implementation status

M3 is implemented as exactly one approval-gated real write vertical slice over
the existing filesystem:write capability:

- adapter: WorkspaceRealWorkflowWriteAdapter
- network policy: none
- effect: mutating
- approval: required; the existing BoundedWorkflowConnector.grant_approval()
  path remains the only approval source
- precondition: the workflow must declare the current file SHA-256 fingerprint;
  ControlledRealWorkflowBackend hashes that declaration into the
  ExecutionEnvelope.precondition_digest and idempotency identity
- execution: the adapter calls the existing bounded WorkspaceConnector.write()
  and exposes no shell, subprocess, browser, credential or arbitrary network path
- postcondition: the adapter re-reads the same file after the write and only
  reports observable success when the resulting content matches the requested
  content
- verification: the existing Phase 5 connector independently re-derives and
  compares the observed artifact digest
- stale state: a fingerprint mismatch fails closed before any write
- ambiguous mutation: the connector records the mutation replay identity before
  verification, so an uncertain post-write outcome cannot be blindly retried
- idempotency: the execution envelope and Phase 5 replay protector both bind the
  operation to the workflow, step, inputs and precondition

The M3 tests cover approval gating, real workspace mutation, stale-precondition
failure, precondition-bound idempotency, ambiguous post-write non-retry, and
secret rejection.

Latest M3 CI validation will be recorded only after the branch check completes.

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