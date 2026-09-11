# Phase J — Digital Task Orchestration

Phase J coordinates existing authority boundaries. It is not an executor, sandbox, lifecycle engine, permission system, or connector implementation.

## Flow

`Task → Intent/Decomposition → Memory Context → Tool/Connector Discovery → Risk → Capability Authorization → Existing Task Planner → Existing Safe Executor → Verification → Memory → Audit → Report`

## Boundary rules

1. **Task intake** normalizes task text and redacts credential-like values. Reports and digests use safe task data.
2. **Planning** delegates to the existing Task Planner. A generated plan is never treated as authorization.
3. **Memory** is observational context only. Corrupt or stale evidence must never grant authority.
4. **Tool discovery** treats the Tool Registry as the executable authority. Connector metadata may constrain selection, never expand permissions.
5. **Risk and authorization** remain owned by the existing Tool Registry and capability policy, including approval, sandbox, network, read/write, and audit requirements.
6. **Execution** is injected only through the existing executor boundary. The orchestrator does not spawn processes, make arbitrary network calls, modify repositories, send messages, or control browsers.
7. **Verification and audit** remain downstream responsibilities of the existing execution/lifecycle/audit architecture. The orchestrator reports structured status without creating a parallel audit chain.
8. **Availability is separate from registration and authorization.** A registered connector can be disabled; an enabled connector is not automatically authorized.
9. **Credentials** are represented only by bounded references. Credential values must not enter metadata, plans, memory, logs, tests, or reports.

## Current-main limitation

Phase J is intentionally based on current `main`. The Phase-G safe executor and Phase-H memory implementation are not on `main` yet, so this branch provides the orchestration contract and safe planning/reporting boundary without reimplementing either component. Once those approved changes land, their existing executor and memory interfaces can be injected without changing the authorization architecture.

## Future domains

GitHub, web research, files, AI providers, projects/repositories, email, calendar, browser automation, and external APIs must each follow:

`declaration → registration → compatibility validation → Tool Registry authorization → existing executor → verification → audit`

No domain receives arbitrary network, browser, shell, credential, payment, destructive, or production authority from orchestration alone.
