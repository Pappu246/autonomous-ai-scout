# Phase 13 — Live GitHub Worker Evidence

## Status

Phase 13 verified the real remote mutation boundary of the persisted approval-to-GitHub worker.

## Verified live path

Inside GitHub Actions, the concrete worker:

1. read and bound the exact `main` HEAD;
2. persisted a deterministic reviewed coding run;
3. created an explicit approval record and single-use claim;
4. created the dedicated worker branch;
5. created exactly one Git commit on that branch with `GITHUB_TOKEN`;
6. returned control without merging or deploying.

Live worker identifiers:

- Workflow canary: Phase 13 Live GitHub Worker Canary v2
- Worker workflow run: `36418755482`
- Expected `main` HEAD: `dd63b4b7a75e31eb783e6afb4b2685b9afad86e2`
- Worker branch: `improvement/phase13-live-worker-36418755482`
- Worker-created commit: `ae097296e8a0c85d35c992b9cbfee0c7f38e006e`
- Draft PR completed as: `#186`
- PR-head CI run: `#2520`
- PR-head CI result: `2298 passed, 6 skipped`
- Final merged commit: `6567ba2c6d2a364acf28d979458e235bba5f0c7c`

## Important boundary

The worker's final `POST /pulls` call was rejected by GitHub for the workflow token even though the workflow explicitly requested `pull-requests: write`. GitHub documents a separate repository/organization setting controlling whether Actions workflows may create or approve pull requests with `GITHUB_TOKEN`. citeturn383567search0turn383567search5

Because that setting could not be changed through the connected repository control plane available here, the already-created worker branch and commit were preserved and the draft PR lifecycle was completed through the connected GitHub control plane without replaying the worker mutation.

This means the live evidence is exact about what was proven:

- **Proven live:** approval-to-worker bridge, exact-head binding, real branch creation, real commit creation, draft-PR/CI observation path, no worker merge/deploy.
- **Environment-gated:** worker-created draft PR via `GITHUB_TOKEN` under the repository's current Actions PR-creation policy.

## Safety

The live candidate modified only `PHASE13_LIVE_WORKER_OUTPUT.md`. No runtime module, credentials, workflow permissions on `main`, deployment configuration or production resource was changed.
