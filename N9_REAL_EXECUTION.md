# N9 — Real Execution Layer

N9 closes the gap between a planned task and an actually executed, verified task.

## Goal

`natural-language task -> bounded plan -> capability authorization -> registered tool -> safe execution boundary -> verification -> audit -> runtime journal`

N9 does not grant unrestricted source writes, deployment, merge, credential handling, or production actions.

## Implemented

- Central execution entry point: `autonomous_agent/execution_engine.py`
- Runtime integration: `autonomous_agent/runtime.py`
- Registered-tool lookup before execution.
- Explicit capability grants before every step.
- Safe sandbox boundary for local operations.
- Controlled connector execution for web, browser, filesystem, Gmail, and Calendar paths when the corresponding connector is supplied and authorized.
- Bounded retries with a hard maximum.
- Per-step verification requirement.
- Hash-chained execution audit.
- Interrupted-run detection with fresh-authorization recovery.
- Runtime journal recording of task outcomes.
- Cross-project memory hooks for execution outcomes.
- Fail-closed behavior for unknown tools, invalid capabilities, unavailable sandbox/audit, incompatible connector definitions, and failed verification.

## N9 acceptance criteria

A safe task such as `inspect repository` must:

1. Produce an executable plan.
2. Select only registered tools.
3. Require the required capability grant.
4. Execute through the centralized execution engine.
5. Return `verified` only when every executed result is verified.
6. Write a valid execution audit.
7. Record the runtime result in the journal.
8. Never replay an interrupted execution automatically.

## Explicit N9 boundary

N9 is an execution layer, not the self-modification layer.

Source patch generation, branch/commit/PR creation, outcome-driven self-improvement, and human-gated merge remain later milestones.