# Gate 3 — Coding Provider Smoke

Gate 3 is the first remaining live-environment readiness gate.

## What this workflow proves

The manual workflow:

1. checks out the exact commit being tested;
2. installs the repository's pinned dependency snapshot;
3. requires an operator-supplied `SCOUT_CODING_PROVIDER_API_KEY`;
4. requires an HTTPS OpenAI-compatible coding endpoint;
5. configures exactly one explicit provider route;
6. runs the existing `autonomous-scout-code` pipeline against the checked-out workspace;
7. requires the normal proposal → patch review → bounded validation path to reach `READY_FOR_APPROVAL`;
8. stops there.

It does **not** approve the action, execute the GitHub worker, merge, deploy, bill, or modify repository source.

## Required operator configuration

In the repository's GitHub Actions secrets, provide:

- `SCOUT_CODING_PROVIDER_API_KEY`

At dispatch time, provide the non-secret provider details:

- provider name
- HTTPS endpoint
- model identifier
- free/paid classification
- whether paid routing is explicitly permitted

A missing credential or invalid endpoint causes the workflow to fail closed.

## Evidence standard

Gate 3 is considered **LIVE-VERIFIED** only after a successful workflow run demonstrates:

- real provider request/response;
- a valid `PatchCandidate`;
- bounded local validation success;
- persisted `READY_FOR_APPROVAL` state;
- no merge/deploy side effect.

A repository test or a mocked provider is not sufficient to promote this gate.
