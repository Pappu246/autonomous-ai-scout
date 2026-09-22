# N22 — Browser Interaction

## Objective

Provide a bounded higher-level browser workflow over the existing `ControlledBrowser` transport, with sequential actions and explicit per-action verification.

## Capability

Supported actions:

- `open` an explicitly allowed HTTPS host;
- `click` an explicitly allowed selector on an allowed HTTPS host;
- `extract` bounded fields/text from an allowed HTTPS host.

A workflow is limited to 12 actions and stops immediately on a failed or unverified action.

## Architecture

```text
task / tool router
     │
     ▼
BrowserWorkflow
     │
     ▼
execute_browser_tool
     │
     ▼
ControlledBrowser
     │
     ▼
injected browser transport
```

## Safety

- HTTPS-only navigation and explicit host allowlists remain enforced by `ControlledBrowser`.
- No arbitrary JavaScript execution or unrestricted navigation is added.
- Selector length, timeout and output bounds remain enforced by the existing browser transport.
- Workflow execution is sequential and bounded.
- Verification failure stops the workflow; later actions are not attempted.

## Capability proof

Runnable offline demo/test:

```bash
pytest -q tests/test_browser_workflow.py
```

The tests simulate the browser transport, demonstrate `open → click → extract`, verify host allowlisting, step limits, and stop-on-verification-failure.

## Limitations after N22

Still deferred:

- desktop/OS UI automation and shell operations (N23);
- authenticated browser sessions/credential brokering (N28);
- hostile-page/prompt-injection defenses (N29);
- advanced computer vision/screen interaction.

## Acceptance gate

N22 is VERIFIED only when browser workflow tests and full CI are green and the workflow preserves the underlying ControlledBrowser safety boundary.
