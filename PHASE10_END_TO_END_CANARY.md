# Phase 10 — End-to-End Canary

Phase 10 adds a real integration canary for the current autonomous-agent stack.

## What it exercises

The canary runs through the actual build_agent() assembly, real WorkspaceConnector,
the canonical digital planner/runtime, sandbox execution, hash-chained audit and
checkpoint/resume paths.

It verifies four operational boundaries:

1. a read-only workspace goal reaches VERIFIED with observable evidence;
2. a filesystem side effect stops for approval and executes only after explicit approval;
3. failed multi-step work resumes without replaying an already verified step;
4. an unavailable/reserved capability fails closed instead of being faked.

## Safety

The suite uses tmp_path only. It does not contact external services, modify the
repository, send mail, change a calendar, deploy infrastructure, or grant new
capabilities.

## Operational meaning

A green Phase 10 canary proves the canonical local digital-agent lifecycle is
wired end-to-end at the repository test level. It is an integration regression
gate, not a claim that every external provider configuration has been live-tested.
