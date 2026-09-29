# Final Repository Audit — 2026-09-28

## Scope

This audit reviewed the current mainline from the earlier Phase/N-series work through the latest Phase 13 closure and provider-readiness changes. The review covered:

- canonical digital-agent routing, authorization, sandboxing, verification and resume;
- approval and consequence policy;
- GitHub worker boundaries;
- coding-provider readiness;
- CI workflow and recent GitHub Actions runtime warnings;
- repository-wide TODO/FIXME/stale-status searches;
- the latest regression-test and CI evidence.

## Findings

### Fixed during this audit

**Resume checkpoint input binding**

The resume checkpoint previously bound progress to the goal/plan/authorization identity but did not include the concrete per-step request arguments. That could allow the same goal to be resumed with different request inputs while reusing a previously verified step.

Fixed in PR #195 by binding the authorization digest to per-step request arguments and credential references. A regression test proves that changed request inputs execute again.

### Fixed before this audit

**Coding-provider readiness visibility**

The local doctor now distinguishes configured coding-provider routes from the presence of required credential environment variables without exposing credential values.

**GitHub Actions runtime warning and supply-chain hardening**

The CI workflow was first updated from actions/checkout@v4 and actions/setup-python@v5 to v7 majors, then pinned to immutable release commit SHAs. Dependabot is now configured to check GitHub Actions and pip dependencies weekly. GitHub's current security guidance recommends full-length commit-SHA pinning for actions. 

## Validation evidence

Latest regression CI on the audited PR:

- checkout integrity: passed
- Python 3.11 setup: passed
- package installation: passed
- test suite: **2299 passed, 6 skipped**
- workflow job conclusion: **success**

Post-merge mainline CI for the security hardening commit also completed successfully with the same test job; the CI log reports **2299 passed, 6 skipped**.

Supply-chain hardening merge commit:

`e256b823ae0a76fdd5e93555c51e84b7e6586fb7`

Dependency validation also accepted pytest 9.1.1 by widening the test extra from `pytest>=8,<9` to `pytest>=8,<10`. Mainline CI validated **2299 passed, 6 skipped** with pytest 9.1.1 after the merge.

Current main commit at this documentation update:

`00cb2359a1a7d478242a8f5c1428038e2ab23a7a`

Earlier audited fix commit:

`d292464dce1e982fc8830aff6af9952ecfe341dd`

## Repository hygiene

Current repository searches found no remaining TODO, FIXME, Node 20 workflow references, stale reserved-status markers from the audited capability model, not-implemented markers, or current known-limitation blockers. The CI workflow now uses immutable action SHAs and weekly Dependabot update checks.

There are no open pull requests after the audited fixes.

## Remaining environment-dependent boundary

A real external coding-provider execution still requires operator-supplied provider credentials and an appropriate target workspace. The repository does not fabricate credentials or silently fall back to an unconfigured provider.

## Audit conclusion

No known repository-level regression remains in the audited areas. The concrete correctness issue found by this audit was fixed, covered by a regression test, passed CI, and merged to main.





### Runtime server security hardening — 2026-09-29 (PR #214)

A deep audit of the optional HTTP runtime server found that /run could execute a natural-language task without authentication if an operator changed the bind address from the safe loopback default to a non-loopback host. PR #214 closes that configuration hazard: loopback binds remain local and unauthenticated, while non-loopback binds now require SCOUT_SERVER_TOKEN, and /run requires the matching X-Autonomous-Scout-Token request header. The task input is also bounded before runtime invocation.

PR #214 merged as 5e7db45f929f6e67d2afa12fc7c4b87f9300be52. Its validation job passed; the post-merge mainline CI was still running at the time this documentation update was prepared.

### Current verified boundary after PR #214

The repository-level implementation currently has:
- immutable GitHub Actions supply-chain pins and weekly Dependabot checks;
- approval-gated writes and permanently denied merge/deploy/billing/destructive paths;
- request-bound durable resume checkpoints;
- network-isolated coding validation with fail-closed behavior;
- inter-process serialization for durable queue/lease/side-effect/trigger state;
- a least-privilege scheduled scout publisher with artifact transfer;
- non-loopback runtime-server authentication;
- 0 open pull requests and 0 open issues at the time of reconciliation.

Still pending outside the code-only boundary:
- the next natural scheduled Scout run after PR #211/#214 for live end-to-end publication proof;
- operator-supplied coding-provider credentials and real target workspace;
- real external connector smoke tests with legitimate accounts;
- main branch protection/ruleset administration;
- formal GitHub release/tag creation.


### Deep persistent-state hardening — 2026-09-29 (PRs #216 and #217)

The multi-process audit was extended beyond the queue/lease/side-effect/trigger stores.

PR #216 found that the hash-chained execution audit and bounded run journal were still file-backed shared state. Concurrent writers could derive the same chain predecessor or race journal compaction. The existing inter-process lock was therefore reused for audit append/verify and run-journal append/read operations. Final PR validation reached **2320 passed, 6 skipped**.

PR #217 then audited the remaining persistent ledgers and memory paths. Approval-audit hash chains, lifecycle-ledger transitions, approval decisions, and the cross-project persistent memory log all had read-modify-write or check-then-write race windows. These paths now share the same inter-process locking model, and process-level regression tests cover concurrent writers and single-winner approval decisions. PR #217 merged as \`cdcaba9a3220ca108d73e4d82e4a9e3efb61cba9\`; mainline CI is running for that merge at the time of this update.

### Current repository status after PR #217

- Main: \`cdcaba9a3220ca108d73e4d82e4a9e3efb61cba9\`
- Open pull requests: 0
- Open issues: 0
- Main branch protection: not configured/reported as unprotected
- Repository rulesets: none returned by the available GitHub connection
- GitHub releases: none published
- Latest scheduled Scout run visible: #126, which failed on pre-#211 code during artifact-path publication
- No scheduled run after the PR #211/#214 fixes has appeared yet, so live publication remains the only unresolved code-independent verification gate

### Final reconciliation — 2026-09-29

This section supersedes the earlier remaining-publication-verification statement above.

Current main:
60c43617ece97d22faedd51da4acabc0cf0d6be8

Post-merge mainline CI:
- workflow run: 36592581802
- exact checkout: 60c43617ece97d22faedd51da4acabc0cf0d6be8
- result: 2320 passed, 6 skipped
- job conclusion: success

Scheduled Scout publication path:
- PR #223 fixed the GitHub state-publication authentication path.
- The isolated live publication canary run 36592476878 completed with both Scout and Publish jobs successful.
- The actual state branch was updated by github-actions[bot] at commit 2d24d6dd09be83185268af928bd804bf322731e4.
- This closes the prior code-level publication-path gap.
- The exact cron trigger itself remains separately unobserved; the same scheduled workflow logic and its full generation-to-publication path were live-exercised successfully.

Phase 13 current-main worker:
- worker run: 36592770917
- worker-created commit: 32a19e4220e842c0f91afc403f9ac73a2ee7f292
- worker-created draft PR: #225
- persisted coding run: EXECUTED
- lifecycle state: EXECUTED
- no merge/deploy performed by worker
- independent normal CI run: 36592944700
- CI: 2320 passed, 6 skipped

Repository hygiene after reconciliation:
- Temporary worker/publication canaries are closed/reset after evidence capture.
- No known TODO/FIXME/PENDING/not-implemented repository markers remain in the audited codebase.
- The production/mainline code changes from this reconciliation are limited to the real PR #223 publication-auth fix and its security-contract test update.
- The live evidence harnesses themselves remain isolated from main.

Remaining boundaries are genuinely external rather than code-completion gaps:
1. A real external coding-provider execution requires operator-supplied provider credentials and a legitimate target workspace.
2. Real external connector smoke tests require legitimate connected accounts/credentials.
3. Main-branch protection/ruleset administration is not configured through the available GitHub connection.
4. No formal GitHub release/tag has been created; the available connected GitHub toolset does not expose release/tag creation.

These boundaries are not being represented as completed without evidence.


## Current mainline reconciliation — 2026-09-30

This section supersedes earlier status snapshots where they conflict with the current mainline.

### Current repository baseline

- Baseline main before this reconciliation: `11a51492efb1c41ee0da8428ded800d1859f3f38`
- Open pull requests: **0**
- Open issues: **0**
- Latest baseline CI: **2352 passed, 6 skipped**
- Latest baseline Windows computer smoke: **5 passed, 1 skipped**
- Latest merged computer-control work: PRs **#228–#232**

### Computer-control completion

The current mainline now includes:

- real native computer-use control rather than metadata-only computer capability;
- PNG desktop screenshots and bounded input actions;
- canonical task-runtime integration;
- adaptive execution propagation;
- bounded provider retries and fail-closed provider errors;
- dedicated final visual verification;
- automatic Windows smoke coverage.

### Documentation / supply-chain finding

The audit identified two maintenance gaps after the feature work:

1. repository status documentation lagged behind the latest mainline;
2. the Windows computer smoke workflow still used mutable `actions/checkout@v4` and
   `actions/setup-python@v5` while the primary CI workflow was already pinned.

This hardening change addresses both:

- the current readiness baseline is recorded in `docs/OPERATIONAL_READINESS_20260930.md`;
- the stale next-phase note is replaced with the real readiness gates;
- the Windows computer workflow is pinned to the same immutable v7 action SHAs used by primary CI;
- regression coverage now rejects the old mutable action references.

### External boundaries that remain genuinely external

The following are intentionally not marked complete without live evidence:

- real external coding-provider execution;
- real Gmail/Calendar/application/document account smoke tests;
- GitHub main-branch protection/ruleset administration;
- formal GitHub release/tag publication.

These are environment or administrative boundaries, not hidden implementation claims.
