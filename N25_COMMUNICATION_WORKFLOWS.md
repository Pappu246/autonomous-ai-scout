# N25 — Gmail / Calendar / Communication Workflows

## Objective

Provide a single higher-level workflow boundary for common communication tasks while preserving each underlying Gmail/Calendar approval and authentication policy.

## Capability

Meeting coordination can be planned as:

```text
email.search
     │
     ▼
calendar.find_free_time
     │
     ▼
optional email.draft       ← approval required
     │
     ▼
optional calendar.event.create  ← approval required
```

N25 does not imply `email.send`. Sending remains an explicit human-review operation in the existing registry.

## Safety

- Read-only email/calendar operations can remain autonomous when their normal capability grants are present.
- Drafting and event creation remain approval-gated.
- Sending email is never inferred from drafting or meeting coordination.
- Existing user-authentication and connector boundaries remain unchanged.

## Capability proof

Runnable test:

```bash
pytest -q tests/test_communication_workflow.py
```

The tests demonstrate read-only authorization, approval-gated draft/event steps, and the invariant that coordination never silently sends an email.

## Limitations after N25

Still deferred:

- persistent episodic/semantic memory (N26);
- long-term context management (N27);
- credential/permission broker (N28);
- prompt-injection defense (N29);
- generalized consequence-aware approval policy (N30).

## Acceptance gate

N25 is VERIFIED when communication workflow tests and full CI are green and no write/send action bypasses its existing approval boundary.
