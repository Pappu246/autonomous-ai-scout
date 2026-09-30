# Final Readiness Harnesses — 2026-09-30

## Current state

- Gate 3 coding-provider smoke: merged in PR #236; live execution still requires an operator-supplied provider credential and a real provider endpoint.
- Gate 4 external connector smoke: manual read-only workflow added; live evidence requires Gmail and Calendar OAuth access tokens.
- Gate 5 GitHub administration: manual verification/configuration workflow added; requires a credential with repository Administration permission.
- Gate 6 formal release: manual release workflow added; requires explicit confirmation from `main` after the exact SHA is fully green.

These workflows are intentionally evidence-producing boundaries. They do not manufacture success when credentials or administration access are missing.

## Completion rule

A remaining gate changes from NOT VERIFIED to LIVE-VERIFIED only after its corresponding workflow has completed successfully against the real external dependency.