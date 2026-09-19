# N12 — Real GitHub Worker

N12 turns the existing approved-change boundary into a worker control plane for real repository changes.

## Flow

`approved improvement -> verify target HEAD -> revalidate patch -> create dedicated branch -> commit reviewed files -> open draft PR -> observe CI/review state`

The worker is deliberately split into two halves:

- **Execute:** `GitHubWorker.execute()` delegates remote mutation to the existing `prepare_draft_pr()` / `GitHubChangeBackend` boundary.
- **Observe:** `GitHubWorker.observe()` reads PR and CI state and reports whether the worker should wait, stop, or hand the change to human review.

## Safety invariants

- Approval is single-use and revalidated immediately before mutation.
- Proposal, patch digest, file manifest, file-content digest, and target HEAD are bound together.
- Protected branches are never used as worker heads.
- Duplicate PRs are rejected.
- Patch review runs before remote mutation.
- CI failures stop the worker; they are not auto-overridden.
- The worker never merges, deploys, changes branch protection, or bypasses review.
- Observation failures fail closed.

## Boundary

The repository still injects the actual GitHub API backend and CI provider. This keeps credentials and provider-specific networking outside the policy core while allowing a real connector/runtime adapter to supply those capabilities.

N12 is therefore a real execution worker, not an unrestricted autonomous merge bot.
