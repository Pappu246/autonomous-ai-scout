# Phase 6 — Post-Change Verification Gate

A created change is not considered successful merely because a branch, commit, or PR exists. The change must be re-verified after creation.

## Gate

`commit identity → PR reviewability → file snapshot → post-change tests → result`

Any failed check keeps the change blocked from merge/deploy. This module does not merge, deploy, alter workflows, or access secrets.

## Safety boundary

The verification layer is read/verify-only. A backend may report remote state and test results, but the verifier has no operation for merge, deployment, billing, secret access, or source mutation.
