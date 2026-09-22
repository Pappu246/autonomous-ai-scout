# N21 — Universal Digital Tool Layer

## Objective

Expose one common contract for discovering, validating, authorizing and invoking registered digital tools, while reusing the existing Tool Registry and Connector Registry.

## Architecture

```text
task / planner
     │
     ▼
UniversalDigitalToolLayer
     │
     ├── discover
     ├── resolve
     ├── validate arguments
     ├── authorize
     └── invoke(adapter)
     │
     ├───────────────┬───────────────┐
     ▼               ▼               ▼
Tool Registry   Connector Registry  Execution adapters
```

## Capability

The layer can discover tools by category/capability/query and exposes connector metadata for the matching registered tools. Invocation always passes through registry authorization before an execution adapter is called.

Examples include files, web, browser, Gmail, Calendar, GitHub, testing, metrics and restricted high-impact tools already present in the registry.

## Safety

- Tool registration remains the source of truth.
- Tool selection does not grant capabilities.
- Approval and audit requirements continue to come from the existing registry policy.
- Unknown tools fail closed.
- Unknown input fields are rejected when the registered schema disallows them.
- No credentials are accepted by this layer; connectors continue to use their existing credential/reference boundaries.

## Capability proof

Runnable focused test:

```bash
pytest -q tests/test_digital_tool.py
```

The test suite demonstrates cross-domain discovery, connector metadata, schema validation, approval enforcement, successful adapter invocation, and unknown-tool fail-closed behavior.

## Limitations after N21

Still deferred:

- browser/computer interaction implementation beyond existing controlled browser transport (N22);
- general OS/shell interaction (N23);
- autonomous credential acquisition/refresh (N28);
- prompt-injection defense and hostile-content isolation (N29);
- autonomous high-impact approval policy (N30).

## Acceptance gate

N21 is VERIFIED only when the universal discovery/invocation tests and full CI are green, the safety boundaries remain intact, and the capability demonstration is available.
