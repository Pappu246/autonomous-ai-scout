# AI Release Handoff — Autonomous AI Scout

## Current verified baseline

- Repository: `Pappu246/autonomous-ai-scout`
- Working branch: `feat/mission-control-ui`
- Current verified code head: `7df4fec927f18ee0277c6e39e18ba3a196362f5f`
- Base `main`: `5434114b33a7b00af053a7c43f9d7181d767b8fb`
- PR: #271
- PR state: open, draft, mergeable
- Main: intentionally untouched

## Verified evidence

- CI run 3361 / 37573969520: PASS
- Full test suite: 2433 passed, 6 skipped
- Gate 3 run 68 / 37573717625: PASS on the current exact SHA
- Gate 3 used the real free Ollama provider path, network-isolated sandbox prerequisite, OpenAI-compatible endpoint probe, bounded coding-provider smoke, diff normalization boundary, and approval boundary.

## Remaining work

### Gate 4 — External connector smoke

Authoritative execution requires a workflow-dispatch run on the final SHA with:

- secret `SCOUT_GMAIL_ACCESS_TOKEN`
- secret `SCOUT_CALENDAR_ACCESS_TOKEN`

The live script is `tools/live_gate4_smoke.py`.

The required result is a completed successful workflow run on the exact release SHA. Do not fabricate or substitute mocked credentials/results.

### Gate 5 — GitHub administration readiness
Gate 5 credential-bearing execution is **trusted-main only** and requires explicit `apply_protection=true` authorization.

Authoritative execution requires a workflow-dispatch run on the final SHA with:

- secret `SCOUT_GITHUB_ADMIN_TOKEN` with repository Administration permission
- input `apply_protection=true`

The workflow verifies current protection, applies the hardened protection configuration, verifies it again, and inspects rulesets. The local/agent-side `tools/ai_finish_release.py` now uses `SCOUT_GITHUB_ADMIN_TOKEN` directly through the GitHub REST API, so `gh` is no longer a prerequisite when that token is present.

Do not use the normal GitHub integration's read-only 403 as a substitute for the admin workflow evidence.

### Gate 6 — Formal release

Do not run this on the feature branch.

Only run on `main` after:

1. exact-SHA CI is green;
2. exact-SHA Gate 3 is green;
3. exact-SHA Gate 4 is green;
4. exact-SHA Gate 5 is green;
5. Gate 5 application step explicitly succeeded;
6. tag matches `pyproject.toml` version (`v0.3.0`);
7. the tag does not already exist;
8. release publication is explicitly confirmed with `confirm=true`.

No merge, deployment, tag, or release should be synthesized.

## Exact task to give Autonomous AI Scout

Paste this into Mission Control:

> Inspect the Autonomous AI Scout release baseline and continue the remaining release verification from the current repository state. First verify the active workspace root is the repository itself, then verify the current branch and exact HEAD SHA. Confirm CI 3361 and authoritative Gate 3 run 69 are green for the verified current SHA. Do not modify main and do not fabricate credentials or external results. For Gate 4, determine whether real SCOUT_GMAIL_ACCESS_TOKEN and SCOUT_CALENDAR_ACCESS_TOKEN credentials are available in the current execution environment; if they are available and the workflow can be legitimately dispatched, execute and verify the authoritative Gate 4 smoke on the exact SHA. For Gate 5, determine whether a repository-admin SCOUT_GITHUB_ADMIN_TOKEN and legitimate workflow dispatch capability are available; if so, execute Gate 5 with apply_protection=true and verify the protection application step. If either gate cannot be legitimately executed, report the exact blocker and do not mark it passed. After any legitimate gate completion, re-check the exact SHA evidence. Do not merge, deploy, tag, or publish a release unless every Gate 6 prerequisite is genuinely satisfied.

## Operational rule

A mission is VERIFIED only when the requested repository operation actually executed against the intended repository/workspace and its result was independently verified. A plan preview, mocked connector response, unrelated local filesystem scan, or stale workflow run is not sufficient evidence.

