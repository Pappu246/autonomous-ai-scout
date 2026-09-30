# Gate 6 — Formal Release

The release workflow is manual and accepts execution only from `main` with explicit confirmation.

Before publishing it:
- checks the exact mainline SHA;
- requires all existing check runs for that SHA to be completed successfully;
- refuses duplicate tags;
- creates a GitHub release against that exact SHA.

The default tag is `v0.3.0`, matching `pyproject.toml`.

A release is not considered published until the workflow itself completes successfully.