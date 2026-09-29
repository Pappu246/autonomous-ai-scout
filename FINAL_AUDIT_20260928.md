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

### Follow-up live workflow finding — 2026-09-29

The hourly scheduled workflow produced a real operational failure in run **36530410005**. The scout itself completed successfully; publication failed because the prior `git push --force-with-lease` used a stale remote-tracking reference and Git rejected the update with `stale info`.

Fixed in PR #203 by hardening the hourly workflow (immutable action SHAs, checkout credential isolation, checkout-integrity verification, and removal of force-push). PR #204 then fixed the state-branch publication model by snapshotting generated state, fetching the current `autonomous-scout-state` branch, overlaying only the five state/report files, and pushing a normal fast-forward commit.

A workflow-security regression contract was added in PR #205. Its CI run completed with **2301 passed, 6 skipped** and the post-merge mainline CI also passed.

The repository connector cannot manually dispatch the scheduled workflow, so the live scheduled publication path will receive its next real verification at the next hourly run. Until that run completes, the publication fix is validated by CI and by the previous failure evidence, but not yet by a post-fix scheduled-run result.


### Follow-up security hardening — 2026-09-29 (PR #207)

The hourly workflow was further tightened so that the scout job runs with `contents: read` only. Generated state is transferred through a one-day artifact, and a separate `publish` job is the only job granted `contents: write`. The publisher checks out the dedicated `autonomous-ai-scout-state` branch directly and performs a normal authenticated fast-forward push. The artifact upload/download actions are pinned to immutable release commit SHAs, and the workflow-security contract now enforces the least-privilege split.

PR #207 merged to main as `d482835f1f267a6689e6f3808db9c01f46905ae5`'s child commit `d482835f1f267a6689e6f3808db9c01f46905ae5`. The PR validation job passed **2301 passed, 6 skipped**. Post-merge mainline CI is running for this merge at the time of this documentation update.

The latest known scheduled worker run remains **36530410005** (#125), which failed only in the old state-publication step before PRs #203/#204/#207. No post-fix scheduled run has appeared yet; the next scheduled execution remains the definitive live verification of the hardened publication path.
