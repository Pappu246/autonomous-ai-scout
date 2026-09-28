# Phase 7 — Live GitHub Evidence Adapter

## Status
Phase 7 adds a concrete read-only GitHub evidence acquisition layer for the Phase 6 verification gate now present on main.

Architecture:
GitHub REST GETs -> typed Phase 6 evidence -> pure Phase 6 verifier -> deterministic PASS / FAIL / BLOCKED.

The adapter does not merge, deploy, create branches, create pull requests, write files, dispatch workflows, or change GitHub state.

## Evidence
- exact base-branch HEAD SHA
- exact pull-request metadata and head repository identity
- complete changed-file metadata plus exact bytes at the requested commit
- workflow runs bound to the exact requested commit SHA

Snapshot bytes are hashed with SHA-256. Symlinks, submodules, binaries, renames, deleted files, oversized content and incomplete pagination remain visible to the Phase 6 fail-closed policy.

## Pagination
Pull-request file and workflow-run reads are bounded to six pages of 100 entries. Exhausting that bound without a terminating short page marks evidence incomplete.

Git tree truncation also marks snapshot evidence incomplete. Incomplete evidence cannot produce a Phase 6 PASS.

## CI aggregation
GitHub can expose both push and pull_request runs for one SHA. Duplicate workflow names are aggregated conservatively: failure-like conclusions dominate success; active runs keep the aggregate incomplete; disallowed events are preserved; and all runs are SHA-bound before evaluation.

## Transport
GitHubApiClient now provides four bounded read helpers used by the adapter:
- read_file_bytes_at_ref
- pull_request_files
- git_tree
- workflow_runs

Those methods are GET-only and require the existing GitHub credential gate. They do not introduce a second mutation authority.

## Operational API
GitHubPostChangeEvidenceProvider.from_env() builds the provider from the existing GitHub API configuration.
provider.verify(request) collects live evidence and invokes the pure Phase 6 verifier.

PASS means only that the exact request has sufficient matching evidence for the deterministic post-change verification gate. It is not merge or deployment approval.

## Non-goals
- automatic merge or deployment
- automatic approval
- branch protection configuration
- workflow dispatch
- credential persistence
- general-purpose mutation orchestration