# Phase 6 — Post-Change Verification Gate

## Status

Phase 6 is implemented as a bounded, read/verify-only library layer. The
repository remains responsible for acquiring evidence; the Phase 6 core never
opens a network connection, reads credentials, runs tests, mutates GitHub,
writes files, merges, deploys, or persists state.

## Gate

The authoritative order is:

1. commit identity
2. PR reviewability
3. exact file snapshot
4. post-change test attestation
5. final result

Any earlier failure blocks later stages. A final PASS is possible only when
all four evidence stages pass for the same repository and exact commit.

## Milestones

### M1 — Pure decision contract

Defines strict immutable request/evidence/result models, bounded detail,
redaction, deterministic canonicalisation/digests, stage ordering, and
fail-closed verdicts. It contains no I/O or provider interface.

### M2 — Commit identity and PR reviewability

Adds a narrow typed read-only evidence surface:
read_commit_identity and read_pull_request. Validation binds repository,
PR number, base/head branches, head repository, and the exact 40-character
head SHA. Open non-draft, unmerged PRs are the only reviewable state.

### M3 — Exact file snapshot

Adds a typed read_file_snapshot observation. A snapshot must be complete,
non-truncated, tied to the exact repository and expected commit, and exactly
match the expected changed-file manifest and content SHA-256 values.

The default policy rejects binary files, symlinks, submodules, renames and
oversized files. Unsafe paths and duplicate entries are rejected.

### M4 — CI/test attestation and final verdict

Adds a typed read_completed_checks observation. Required checks must be for
the exact expected commit SHA, expected repository, completed, successful,
produced by an allowed event (push or pull_request), and from an allowed
workflow (CI). Required checks are present exactly once.

Old-SHA, pending, queued, neutral, skipped, cancelled, timed-out,
action-required, wrong-workflow, wrong-event, missing and contradictory
evidence cannot produce PASS.

verify_phase6 recomputes the five ordered stages and produces a frozen final
report. Request, evidence and policy digests remain separately visible.

## Authority boundary

The Phase 6 production modules expose no merge/deploy operations, branch
creation/update, PR creation/update, file writes, process/shell execution,
arbitrary HTTP transport, credential/environment access, workflow dispatch,
persistence, or generic callback execution.

The provider protocols are observation-only and intentionally do not embed a
GitHub transport. A future transport adapter, if separately authorized, must
remain outside this verifier boundary and supply only typed read evidence.

## CI relationship

The repository CI workflow already checks out the exact PR head SHA and uses
contents: read. Phase 6 consumes the resulting evidence contract; it does
not start or mutate CI.

## Explicit limitations

Phase 6 does not currently provide a live GitHub evidence adapter, automatic
worker/store integration, lifecycle integration, branch-protection rules, or
merge automation. Those would add authority beyond the bounded verification
core and require separate authorization.

## Persistence rule

Each milestone is persisted independently before the next one. Phase 6 does
not reopen or modify the frozen Phase 1–5 implementation.

## Validation

M5 validation includes Phase 6 targeted tests, existing verification-related
tests, full repository CI, static AST authority review, and regression
confirmation that Phase 1–5 remain unchanged.

The correct outcome of this layer is evidence plus a deterministic blocking
or passing verdict; it is never an instruction to merge or deploy.
