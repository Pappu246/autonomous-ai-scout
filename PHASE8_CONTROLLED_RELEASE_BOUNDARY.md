# Phase 8 — Controlled Release Boundary

## Status

Phase 8 introduces a **decision-only release boundary** on top of the Phase 6
post-change verification contract and the Phase 7 live GitHub evidence adapter.

The boundary is:

```
artifact identity
      ↓
exact request binding
      ↓
Phase 6 post-change verification
      ↓
CI + review-policy result
      ↓
explicit release approval
      ↓
allowlisted non-production target
      ↓
ELIGIBLE
```

An `ELIGIBLE` result is **not** a deployment command and does not grant a new
mutation capability.

## Safety contract

- `production` and `prod` targets are always rejected.
- Default release targets are only `sandbox` and `staging`.
- Artifact identity must match exactly.
- Verification must belong to the exact request digest.
- Phase 6 verification must be `PASS`.
- Review policy must explicitly pass.
- CI must be reported as passed.
- Release approval must be explicitly true.
- The result always reports `deployment_permitted = false`.
- The module has no network, filesystem, credential, subprocess, merge, or deploy
  implementation.

## Why this is the next boundary

Phase 7 made live GitHub evidence acquisition concrete. Phase 8 turns that
evidence into a reusable **release eligibility decision** without crossing the
existing worker's merge/deploy boundary.

The release gate is deliberately separate from the GitHub worker and from the
pure Phase 6 verifier: each layer keeps one authority.

## Non-goals

- production deployment
- automatic merge
- workflow dispatch
- infrastructure mutation
- secret persistence
- automatic approval
