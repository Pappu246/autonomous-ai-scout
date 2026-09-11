# Phase I — Connector Architecture

The connector layer is **declarative and non-authoritative**. A registered connector is not automatically enabled, authorized, or executable.

## 1. Connector declaration

`ConnectorSpec` describes a connector identity, category, capabilities, scopes, authentication method, credential-handling mode, network/read-write/risk requirements, approval/sandbox/audit requirements, availability state, version, schema version, and its explicitly registered tools.

Registration validates the connector against the existing `ToolRegistry`. Duplicate identities, malformed schemas, unknown tools, and contract mismatches fail closed.

Credentials are represented only by references/identifiers. Raw credential values are not connector metadata.

## 2. Connector authorization

The connector registry does not create a second permission system. Authorization delegates to the existing `ToolRegistry`, which remains bound to the existing capability policy.

The compatibility chain is:

`ConnectorSpec → ToolSpec → Capability Policy → Sandbox → Lifecycle → Approval → Audit`

Connector metadata may not weaken or expand any ToolSpec contract. In particular it cannot:

- grant a denied capability;
- expand network access;
- turn read-only work into write work;
- lower risk;
- remove approval;
- remove sandbox requirements;
- remove audit requirements; or
- substitute connector authentication for tool authorization.

## 3. Connector execution

Phase I does **not** implement unrestricted external connector execution.

The safe adapter boundary is:

`validate request → resolve registered tool → authorize through Tool Registry → existing Safe Executor → verify result → audit`

The adapter only prepares an authorization-bound request and delegates execution to the existing execution boundary. It does not contain a second executor, connector-specific permission model, arbitrary network client, browser controller, or credential store.

Future real connectors must plug into this boundary rather than creating a parallel execution path.

## 4. Connector availability

Availability is separate from registration and authorization:

- **registered** means the connector declaration passed contract validation;
- **enabled** means the connector is available for authorization attempts;
- **authorized** means the existing Tool Registry and capability policy permitted the requested registered tool under its current sandbox/approval/audit conditions;
- **executable** means an authorized request has subsequently crossed the existing lifecycle/executor/verification boundary.

Future email, calendar, and browser connectors are declared but disabled and expose no executable registered tool. Their presence in the catalog must never be interpreted as permission to act.

## Future orchestration

Phase J can discover connector declarations as metadata, but it must preserve the same authority chain. The long-term flow is:

`User Task → Task Understanding → Task Decomposition → Memory Context → Tool/Connector Discovery → Risk Analysis → Capability Authorization → Execution Plan → Existing Safe Executor → Verification → Memory → Audit → Report`

Official APIs should be preferred for future digital work. Browser automation remains a controlled future capability, not arbitrary computer control. Payments, destructive actions, unauthorized access, unrestricted network access, and unrestricted credentials remain outside autonomous authority.
