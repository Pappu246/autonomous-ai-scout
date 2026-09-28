# PHASE 11 — Real GitHub Execution Canary

## Purpose

Phase 10 proved the canonical local digital-agent lifecycle. Phase 11 verifies the
remote GitHub mutation boundary against the current repository head without
bypassing the project's approval and merge safety rules.

## Evidence scope

The repository already contains the persisted approval-to-worker integration:

- reviewed coding run persisted in `state/coding_runs/`
- explicit approval transition
- lifecycle transition through `APPROVED` and `CLAIMED`
- one-shot execution claim
- exact repository / patch / file / expected-HEAD identity checks
- GitHub worker branch + commit + draft-PR boundary
- CI observation
- no worker-side merge or deploy

The Phase 11 canary adds a harmless repository-side evidence artifact and uses
the live GitHub pull-request path to verify:

1. branch creation from the exact `main` head used for this canary;
2. one commit on the canary branch;
3. creation of a draft pull request;
4. GitHub Actions execution against the PR head;
5. post-change CI observation from the remote repository.

## Safety boundary

This canary is documentation-only. It does not change runtime behaviour, access
controls, credentials, provider configuration, deployment configuration, or
production state.

The resulting PR is intentionally draft-only. It is not auto-merged or deployed.

## Limitation

The repository's Python GitHub worker requires runtime provider/environment
configuration that is not available inside this ChatGPT execution environment.
Therefore the live remote portion is validated through the connected GitHub
mutation and observation surface, while the worker's full persisted approval
bridge remains covered by the repository's deterministic integration tests
(`tests/test_coding_run_store.py` and `tests/test_approved_coding.py`).

This separates verified evidence from any unsupported claim that the worker
itself was executed inside this environment.
