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



## Current repository status — 2026-09-29

**Main:** \`546f026c4d32e98a2b90d182ab6555088d38826d\`

**Repository hygiene:** 0 open pull requests and 0 open issues at the time of this reconciliation.

**CI:** Mainline CI for the current main commit passed after PR #212. The final PR #212 validation run passed **2309 passed, 6 skipped**. Earlier live validation also passed after PRs #210 and #211.

**Branch protection:** the main branch is currently reported by GitHub as unprotected. No repository rulesets are currently returned by the available GitHub API connection. This is an administrative control still requiring repository-owner configuration; it is not claimed as completed by the codebase.

**Releases:** no GitHub releases are currently published. A formal release/tag is an optional release-management step, not a runtime blocker.

## Deep live workflow evidence — 2026-09-29

The scheduled workflow has now produced an additional real failure after the least-privilege split. Run **36575216337** (#126) reached the end of the \`scout\` job successfully: checkout, Python setup, install, integrity check, tests, scout execution, and state-artifact upload all passed. The separate \`publish\` job checked out the state branch and downloaded the artifact successfully, including digest verification, but failed during state copy because the artifact action input used the literal string \`$RUNNER_TEMP/scout-state\`. GitHub Actions action inputs do not perform shell expansion, so the publisher looked for a literal path containing \`$RUNNER_TEMP\` and could not find \`latest_report.md\`.

PR **#211** corrected this to the GitHub Actions expression \`\${{ runner.temp }}/scout-state\` and added a regression contract that rejects the broken form. PR #211 merged as \`8b3f1c1d2da92b4894efac20ed13cf766df65c65\`, and its mainline CI passed.

This live failure is valuable evidence: the scout execution path itself is working, and the remaining defect was isolated to the publisher's transfer-path syntax rather than the agent or artifact generation.

## Deep security/runtime hardening — 2026-09-29

### PR #210 — network-isolated coding validation

The AI coding validation runner previously used a restricted command allowlist, \`shell=False\`, a filtered environment, and temporary workspaces, but did not require Linux network isolation. The validator was hardened to fail closed when isolation cannot be established, to probe supported Linux isolation modes on GitHub-hosted runners, to reuse the configured Python interpreter, and to restore temporary-workspace ownership after privileged validation. Final PR validation passed **2309 passed, 6 skipped** before merge.

### PR #212 — cross-process durable-state serialization

A deeper runtime audit found that \`threading.Lock\` only protects one Python process. The durable queue, concurrency leases, external-side-effect ledger, and persistent trigger registry are shared through files and therefore also require inter-process coordination when multiple worker processes are active. PR #212 added a portable standard-library inter-process file lock and bound it to those state stores. The trigger registry also reloads persistent state while holding the process lock so separate workers do not make decisions from stale in-memory cooldown data.

PR #212 merged as \`546f026c4d32e98a2b90d182ab6555088d38826d\`. Final validation passed **2309 passed, 6 skipped** and the post-merge mainline CI passed.

## Remaining work, explicitly bounded

### Live verification still pending

The latest scheduled run visible from GitHub is still run **#126**, which ran on the pre-#211 commit \`0932f20afa8fd4fdb28e183df7f3e74e1b0856f\`. The next natural hourly run after PR #211/PR #212 is therefore still the definitive end-to-end proof of the corrected publisher path. The available GitHub connector cannot manually dispatch this workflow, so no artificial live success is claimed.

### Environment-dependent work

A real external coding-provider execution still requires operator-supplied provider credentials and a target workspace. Gmail, Calendar, browser/computer, and other real external connectors likewise require legitimate credentials and an appropriate runtime environment. The repository does not fabricate any of these prerequisites.

### Administrative release hardening

Main branch protection/rulesets and a formal GitHub release/tag are not currently configured. These are repository-administration/release-management tasks rather than missing agent-core implementation, and the available GitHub connection cannot configure them from this session.
