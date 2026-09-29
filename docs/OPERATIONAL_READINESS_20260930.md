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
- Baseline main before this hardening change: `11a51492efb1c41ee0da8428ded800d1859f3f38`
- Open pull requests at audit time: **0**
- Open issues at audit time: **0**
- Baseline CI: **2352 passed, 6 skipped**
- Baseline Windows computer smoke: **5 passed, 1 skipped**
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
| Windows CI smoke | **VERIFIED** | Hosted Windows smoke is automatically triggered for computer-control changes; action dependencies are now pinned to immutable SHAs in this hardening change. |
| Scheduled state publication | **LIVE-VERIFIED** | Existing Phase 13/current audit evidence confirms successful Scout + Publish canary and authenticated state-branch update. |
| Approval → real GitHub worker | **LIVE-VERIFIED** | Existing Phase 13/current audit evidence confirms a worker-created draft PR and successful ordinary PR CI revalidation. |
| External coding-provider execution | **NOT LIVE-VERIFIED** | Requires operator-supplied provider credentials and a legitimate target workspace. The repository must fail closed when these are absent. |
| Real Gmail/Calendar/application/document external accounts | **NOT LIVE-VERIFIED** | Requires legitimate connected accounts and operator credentials; deterministic repository coverage is not a substitute for account smoke evidence. |
| Main branch protection | **ADMINISTRATIVE** | Must be verified/configured through GitHub repository administration; the connected API cannot read the protection resource in this session. |
| GitHub rulesets | **UNVERIFIED** | No rulesets were returned by the available connection. Treat this as an administrative verification item, not as proof of absence. |
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
