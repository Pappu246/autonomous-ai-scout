# Phase 13 — Live GitHub Worker Evidence

## Status

**Phase 13 is complete.** The worker mutation path and the normal pull-request CI gate have both been live-verified.

Phase 13 verified the real remote mutation boundary of the persisted approval-to-GitHub worker, including a worker-created draft PR after the repository Actions PR-creation permission was enabled.

## Verified live path

Inside GitHub Actions, the concrete worker:

1. read and bound the exact `main` HEAD;
2. persisted a deterministic reviewed coding run;
3. created an explicit approval record and single-use claim;
4. created the dedicated worker branch;
5. created exactly one Git commit on that branch with `GITHUB_TOKEN`;
6. created a draft PR itself with `GITHUB_TOKEN`;
7. returned control without merging or deploying.

### Fresh live worker run

- Workflow: Phase 13 Live GitHub Worker Canary v3
- Workflow run: `36454392200`
- Expected `main` HEAD: `e835af1b12adcdb20367a046a326fa6dcbb9b889`
- Worker branch: `improvement/phase13-live-worker-36454392200`
- Worker-created commit: `137526e8c40792210eac6969079a8f85b9279a35`
- Worker-created draft PR: `#188`
- PR author: `github-actions[bot]`
- PR diff: one file, one line changed in `PHASE13_LIVE_WORKER_OUTPUT.md`

This run proves the repository Actions token can cross the approval-gated mutation boundary and create the draft PR itself. No connected control-plane PR creation was needed for this run.

## CI observation boundary — historical run

The worker then entered its bounded PR-observation loop. The PR's normal CI run was created as workflow run `36454434980`, but GitHub returned the run with conclusion `action_required`. The worker therefore failed closed after its observation budget without merging or deploying.

The earlier `action_required` result was an environment-policy observation at the time of run `36454434980`. It is no longer a current blocker: a fresh normal `pull_request` canary completed successfully as run `36458592757`, and the evidence was subsequently persisted to main.

The draft PR was intentionally closed without merge, and the worker/test branches were reset to the verified `main` SHA after evidence collection.

## Previous live evidence

An earlier Phase 13 canary established the same approval-to-worker bridge and real branch/commit path but could not create the PR from `GITHUB_TOKEN` under the repository's then-current Actions policy. That earlier limitation is superseded for PR creation by the fresh run above.

## Safety

The fresh worker mutation targeted only the existing harmless documentation line in `PHASE13_LIVE_WORKER_OUTPUT.md`. No runtime module, credentials, production deployment configuration, or production resource was changed. The draft PR was not merged.

## CI gate revalidation after Actions permission change

A fresh harmless draft PR canary was run after the repository Actions permission change:

- Canary PR: #190
- Head commit: `3e1a856122d455267edb18849ae384d87af90637`
- Normal `pull_request` CI run: `36458592757`
- CI result: **success**
- The canary PR was closed without merge.

This revalidates that the repository's normal pull-request CI path now executes successfully under the updated Actions configuration. It does not retroactively change the earlier worker observation run `36454434980`, which remains an `action_required` historical run.

## Current final live-worker revalidation — 2026-09-29

The current main head was bound and exercised after the repository Actions permission was enabled.

- Current main HEAD: 60c43617ece97d22faedd51da4acabc0cf0d6be8
- Worker workflow run: 36592770917
- Worker branch: improvement/phase13-live-worker-36592770917
- Worker-created commit: 32a19e4220e842c0f91afc403f9ac73a2ee7f292
- Worker-created draft PR: #225
- PR author: github-actions[bot]
- Approval: explicit and single-use claim
- Persisted coding run: EXECUTED
- Lifecycle state: EXECUTED
- Worker merge/deploy: not performed

The worker-created PR initially observed CI as pending because the originating mutation used GITHUB_TOKEN. An independent no-file-change follow-up commit was then added to the same PR head solely to trigger the ordinary CI path:

- CI-trigger commit: 384291d8b9d16835bf22a82be541c5a4803e3d5f
- Normal CI run: 36592944700
- CI result: success
- CI test result: 2320 passed, 6 skipped
- Exact CI checkout: 384291d8b9d16835bf22a82be541c5a4803e3d5f

This current-main run is the authoritative Phase 13 evidence. The draft PR was intentionally not merged.

## Current live scheduled-publication-path revalidation — 2026-09-29

The scheduled Scout full generation-to-publication path was exercised in an isolated canary after the publication-auth fix in PR #223:

- Fix PR: #223
- Fix merge commit: 60c43617ece97d22faedd51da4acabc0cf0d6be8
- Publication canary run: 36592476878
- Scout job: success
- Publish job: success
- Published state branch: autonomous-scout-state
- Published state commit: 2d24d6dd09be83185268af928bd804bf322731e4

The live path covered exact checkout integrity, dependency installation, full regression tests, Scout execution, generated-state verification, artifact transfer, state-branch checkout, and the final GitHub state/report push. The exact cron-trigger event was not separately observed; the actual Scout and publication path itself was live-verified.

## Final safety boundary

Temporary live-canary branches and draft PRs are closed/reset after evidence collection. No production deployment, merge, billing, destructive action, or credential material was performed by the worker.
