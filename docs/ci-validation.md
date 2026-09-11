# CI validation

This file intentionally makes no product/runtime changes. It documents the required validation contract for incremental Scout changes.

## Required checks

- `pytest -q` must pass through `.github/workflows/ci.yml` on every push and pull request.
- A change is not considered validated merely because a commit exists; the corresponding workflow run must complete successfully.
- Missing status checks are treated as a validation blocker and must not be bypassed by merging or deploying.

## Safety

CI validation does not change the Scout's approval, lifecycle, execution, deployment, provider-policy, billing, or secret-handling gates.
