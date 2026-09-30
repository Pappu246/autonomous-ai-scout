# Operational Readiness — 2026-09-30

## Purpose

This document is the current readiness baseline for **Autonomous AI Scout** after the native
Windows computer-use work landed on main.

The project is evaluated in four distinct states:

1. **Implemented** — the repository contains the capability.
2. **Repository verified** — deterministic tests and GitHub CI exercise the capability.
3. **Live environment verified** — the capability has been exercised against a real external
   account, provider, or desktop environment.
4. **Administrative boundary** — completion depends on GitHub/operator configuration that the
   connected repository tool cannot safely fabricate.

A capability is not promoted from one state to the next without evidence.

## Current baseline

- Repository: `Pappu246/autonomous-ai-scout`
- Current mainline SHA: `c92e9e786445ae4c58d8418125b92b0465d3e74a`
- Latest functional hardening baseline before final-gate harnesses: `108f639d524b5bc25a1e2d4d645a25be47c99fae`
- Gate 3 smoke workflow merged in PR **#236** at `a748b386666c00d5f909e618dbaaaf60dcb2cb6b`
- Open pull requests at audit time: **0**
- Open issues at audit time: **0**
- Latest post-merge CI evidence: **2355 passed, 6 skipped** on the hardening baseline; current `main` (`c92e9e...`) additionally has `test`, `scout`, and `publish` check-runs all completed successfully on the exact mainline SHA.
- Current Windows computer smoke evidence: **5 passed, 1 skipped**
- Latest computer-control changes: PRs **#228, #229, #230, #231, #232**
- No GitHub release is currently published.

## Gate matrix

| Gate | State | Evidence / remaining requirement |
|---|---|---|
| Core autonomous runtime | **VERIFIED** | Canonical planning, authorization, execution, observation, verification, recovery and resume are implemented and covered by the regression suite. |
| Durable safety state | **VERIFIED** | Queue, leases, side effects, triggers, audit/journal, approvals, lifecycle ledger and persistent memory have cross-process locking coverage. |
| Native Windows computer control | **VERIFIED** | Real bounded desktop loop, screenshots, input primitives, approval, replay protection and dedicated final visual verification are implemented. |
| Adaptive computer execution | **VERIFIED** | Computer connector/request propagation is covered through adaptive execution and task core regression tests. |
| Computer provider resilience | **VERIFIED** | Bounded retry rules and fail-closed provider failure conversion are covered by dedicated tests. |
| Windows CI smoke | **VERIFIED** | Hosted Windows smoke is automatically triggered for computer-control changes; action dependencies are pinned to immutable SHAs. |
| Dependency reproducibility | **VERIFIED** | `requirements.lock` is consumed by CI, Windows smoke, and the scheduled Scout workflow; the latest green CI resolved the pinned set successfully. |
| Scheduled state publication | **LIVE-VERIFIED** | Revalidated 2026-09-30 by workflow run **36684385846**; Scout and Publish both succeeded and state branch advanced to **72e823d**. |
| Approval → real GitHub worker | **LIVE-VERIFIED** | Existing Phase 13/current audit evidence confirms a worker-created draft PR and successful ordinary PR CI revalidation. |
| External coding-provider execution | **NOT LIVE-VERIFIED** | Requires operator-supplied provider credentials and a legitimate target workspace. The repository must fail closed when these are absent. |
| Real Gmail/Calendar/application/document external accounts | **NOT LIVE-VERIFIED** | Requires legitimate connected accounts and operator credentials; deterministic repository coverage is not a substitute for account smoke evidence. |
| Main branch protection | **NOT VERIFIED / CURRENTLY UNPROTECTED** | Live branch metadata currently reports `protected: false` and required status checks are off. This must be configured and then re-verified through GitHub repository administration. |
| GitHub rulesets | **UNVERIFIED** | The available connection currently returns an empty ruleset collection; treat this as unverified until checked with repository administration access. |
| Formal GitHub release/tag | **NOT PUBLISHED** | No release is currently published. A formal release requires a supported release/tag creation path plus release evidence. |

## Release policy

The project must not describe itself as fully production-operational merely because deterministic
CI is green. The release record should distinguish:

- repository implementation;
- deterministic CI evidence;
- live external evidence;
- operator/admin configuration.

Missing provider credentials, external accounts, desktop availability or GitHub administrative
controls are **not** converted into synthetic success.

## Next implementation order

1. Keep this readiness record synchronized with the actual mainline.
2. Keep all GitHub Actions used for production/security-sensitive workflows pinned to immutable SHAs.
3. Run real coding-provider execution only when a legitimate provider credential and target workspace
   are available.
4. Perform controlled external connector smoke tests only against legitimate connected accounts.
5. Verify/configure main branch protection and required checks through GitHub administration.
6. Create a formal release/tag only after the release checklist and evidence are complete.
7. Only then start the next feature-generation cycle.

## Explicit non-goals

This readiness document does not authorize automatic merge, deployment, billing, credential bypass,
mass-destructive computer actions, or unrestricted shell execution.


## Autonomous scanner finding reconciliation

The 2026-09-30 Scout report correctly surfaced a high-risk-looking PEM fixture in
`tests/test_auth_broker.py`, but the value was deliberate test data rather than a credential.
The fixture has been rewritten so the secret pattern is assembled at runtime, preserving the
security assertion without placing a literal private-key marker in source.

The same Scout run reported a low-severity lockfile gap. This hardening change adds and consumes
`requirements.lock` across CI, Windows smoke, and scheduled Scout execution, and the dependency
scanner recognizes that explicit lock strategy.


## Latest code-level reconciliation

The hardening merge 108f639d524b5bc25a1e2d4d645a25be47c99fae is the latest verified code baseline. Its post-merge CI and Windows smoke both passed.

The secret-scan finding from the prior Scout report was traced to deliberate test-fixture source text, not a credential. The fixture was rewritten so the private-key-shaped value is constructed at runtime while the authentication rejection assertion remains intact.

The dependency reproducibility finding was closed by requirements.lock, which is consumed by the primary CI, Windows computer smoke, and hourly Scout workflows.

## Final-gate harness reconciliation — 2026-09-30

The readiness execution boundaries are now represented in the repository:

- **Gate 3:** merged manual coding-provider smoke workflow. It requires a real provider credential and ends at `READY_FOR_APPROVAL`; live verification is still pending.
- **Gate 4:** manual read-only Gmail + Calendar smoke workflow plus deterministic application/document safety coverage. Live verification requires legitimate OAuth access tokens.
- **Gate 5:** manual GitHub administration verification/configuration workflow. It requires a repository Administration credential and defaults to read-only; applying protection requires an explicit workflow input.
- **Gate 6:** manual exact-SHA release workflow. It requires dispatch from `main`, a fully green check set, an unused tag, and explicit publication confirmation.

These harnesses reduce the remaining gaps to real environment credentials/admin actions rather than missing implementation. A gate remains NOT VERIFIED until its corresponding live workflow has actually completed successfully.
