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
