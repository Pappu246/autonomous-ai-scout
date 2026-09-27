# Phase 6 — Controlled Real Workflow Execution

Status: **M1 IMPLEMENTED — M2 PENDING**

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