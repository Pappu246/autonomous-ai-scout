# N28 — Authentication / Credential / Permission Broker

## Objective

Separate secret material from task state by resolving credentials through scoped references and short-lived in-memory access, while checking explicit permission scopes before access.

## Architecture

```text
tool/request
    │
    ▼
CredentialRef
    │
    ▼
PermissionBroker
    │
    ├── scope allowed? ── no ──► DENY
    │
    ▼
CredentialBroker
    │
    ├── injected provider
    └── bounded ephemeral access
    │
    ▼
connector consumer
```

## Capability proof

Runnable test/demo:

```bash
python examples/n28_auth_broker_demo.py
pytest -q tests/test_auth_broker.py
```

The demonstration shows a permitted read scope producing an ephemeral lease handle, a denied scope request, and confirms the returned handle contains no raw secret.

## Safety

- Credential references must be non-secret identifiers.
- Credential material is provided only by an injected provider and is never written by the broker.
- Lease duration is bounded to 300 seconds.
- Permission scope is checked before credential access.
- Lease handles are random opaque identifiers and do not contain raw credential material.
- Lease use is single-use and expiry is checked immediately before consumption.
- The broker does not itself broaden Tool Registry authorization or approval policy.

## Limitations after N28

Still deferred:

- prompt-injection defense and credential exfiltration protections (N29);
- consequence-aware approval policy (N30);
- external side-effect idempotency/transactions (N31).

## Acceptance gate

N28 is VERIFIED when permission denial, ephemeral credential access, bounded leases, secret-reference validation, and full CI are green.
