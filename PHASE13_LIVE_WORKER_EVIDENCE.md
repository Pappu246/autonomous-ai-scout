# Phase 13 — Live GitHub Worker Evidence

## Status

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

## CI observation boundary

The worker then entered its bounded PR-observation loop. The PR's normal CI run was created as workflow run `36454434980`, but GitHub returned the run with conclusion `action_required`. The worker therefore failed closed after its observation budget without merging or deploying.

This is now the remaining environment-dependent boundary:

- **Proven live:** exact-head binding, persisted approval, single-use claim, worker branch creation, worker commit creation, and worker-created draft PR.
- **Environment-gated:** successful execution of the normal `pull_request` CI workflow for a PR created by `github-actions[bot]` when GitHub reports `action_required`.

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
