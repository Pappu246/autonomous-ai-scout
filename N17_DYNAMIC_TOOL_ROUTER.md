# N17 — Dynamic Tool Selection Router

## Objective

Move tool selection out of hard-coded per-intent lists and into one deterministic router over the existing Tool Registry. The router chooses the narrowest tool set implied by the task text while leaving authorization, approval, sandbox, and audit decisions to the existing boundaries.

## Capability

Examples:

| Request | Selected tools |
|---|---|
| `run tests` | `github.inspect` → `tests.run` |
| `run lint` | `github.inspect` → `lint.run` |
| `read this page: https://example.com` | `web.read` |
| `research this topic` | `web.search` → `web.read` → `web.extract` → `web.compare` |
| `draft an email` | `email.draft` |
| `list files in the workspace` | `filesystem.list` |
| `automate browser navigation` | `browser.open` |
| `automate the deployment` | no executable mapping; fail closed |

## Safety boundary

- The Tool Registry remains the only source of executable tool definitions.
- Selection does not grant capabilities and does not bypass approval.
- Restricted tools such as `email.send`, `github.change`, `github.merge`, and deployment remain approval-gated or permanently denied by existing policy.
- Missing tools in a custom registry are surfaced as missing required candidates and cause planning to fail closed.
- Ambiguous automation requests with no safe registered mapping remain blocked.

## Architecture

```text
task request
    │
    ▼
intent classification
    │
    ▼
DynamicToolRouter
    │
    ├── candidate tools
    ├── registry availability
    └── selection rationale
    │
    ▼
Task Planner
    │
    ▼
Capability / Approval / Sandbox / Audit
    │
    ▼
Existing Execution Engine
```

## Tests

N17 adds focused coverage for web reads, email drafting, workspace listing, safe browser automation, generic automation fail-closed behavior, missing registered tools, and planner integration.

## Capability proof

After N17, the same task entry point can choose different registered tools from the wording of the request instead of always expanding to one static list for an intent. The selected tool set is deterministic and auditable.

Runnable capability demo:

```bash
python examples/n17_tool_router_demo.py
```

The demo prints the request, inferred intent, selected registered tools, and routing rationale for multiple domains, including a fail-closed unsafe automation request.

Runnable test command:

```bash
pytest -q tests/test_tool_router.py tests/test_task_planner.py
```

## Limitations after N17

Still deferred:

- observation-driven verification and adaptation (N18);
- long-horizon task DAG planning (N19);
- background execution (N20);
- general-purpose digital-tool discovery beyond registered tools (N21+);
- model-assisted tool choice; N17 is deterministic and policy-first.

## Acceptance gate

N17 is VERIFIED only when the implementation and tests are merged, merge-head CI is green, dynamic selection is exercised for multiple domains, and unsafe/unknown tool requests still fail closed.
