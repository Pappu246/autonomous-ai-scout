# Phase J — Digital Task Orchestration

Phase J coordinates existing safety boundaries; it does not replace them.

## Flow

`Task → Intent/Decomposition → Memory Context → Tool + Connector Discovery → Risk Analysis → Capability Authorization → Existing Task Planner → Existing Safe Executor → Verification → Memory → Audit → Report`

## Authority boundaries

- **Task intake** creates a deterministic task digest and redacts credential-like values.
- **Task planning** delegates intent, decomposition, risk and tool selection to the existing Phase-F planner and Tool Registry.
- **Memory** is observational evidence only. Retrieved memory never grants capability, approval, sandbox access, network access, or lifecycle authority.
- **Connector discovery** can only constrain a registered tool. A connector cannot create execution authority or a new permission model.
- **Authorization** is always revalidated against the current Tool Registry and capability policy. Generated plans are not authorization tokens.
- **Execution** is an injected adapter for the existing Phase-G executor. The orchestrator does not implement subprocess, network, browser, repository, email, payment, credential, or destructive execution.
- **Lifecycle** is an injected adapter over the existing lifecycle system. The orchestrator does not define a second lifecycle ledger.
- **Audit** is an injected adapter over the existing audit system. The orchestrator emits digests, step references and state only.
- **Verification** requires a non-empty executor result. Interrupted work is reported as `INTERRUPTED` and is never automatically replayed.

## Future digital work

GitHub, web research, files, email, AI providers, calendar, browser automation, and external APIs may be represented by declarations/adapters later. Execution becomes available only after registration, compatibility validation, capability authorization, existing execution-boundary admission, verification and audit. No unrestricted connector execution is enabled by Phase J.

## Availability is not authority

A registered connector is not automatically enabled. An enabled connector is not automatically authorized. Authorization is evaluated for every planned step against the current Tool Registry and capability policy.

## Project isolation

Project identifiers are carried as metadata and are not permission grants. Repository selection must come from the project/repository registry when integrated with digital work; repository names must never be hardcoded into orchestration policy.
