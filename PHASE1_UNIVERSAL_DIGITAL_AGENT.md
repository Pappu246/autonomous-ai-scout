# Phase 1 — Universal Digital Agent Foundation

Phase 1 changes what the product *is*. Before this phase the system was best
described as an autonomous repository/engineering scout that happened to have
filesystem, web, mail, calendar and browser connectors bolted on. After this
phase it is a **bounded general-purpose digital agent** whose capability domains
are peers, and GitHub is one of them.

Phase 1 builds the architecture. It deliberately does **not** implement
computer control, application adapters or document processing.

---

## The product statement

> A user gives the agent a digital task in natural language. The agent
> determines which digital capabilities are required, executes the work through
> safe registered adapters, verifies the real-world result, and asks for human
> approval only when the action has meaningful side effects or elevated risk.

The user says:

```text
organize today's downloaded PDFs
```

not:

```text
use filesystem.list then filesystem.read then filesystem.write ...
```

---

## Canonical lifecycle

```text
User Goal
   │
   ▼
Intent Understanding      digital/intent.py     understand_goal()
   │
   ▼
Task Planning             digital/planner.py    CapabilityPlanner.plan()
   │
   ▼
Capability Selection      digital/catalog.py    CapabilityCatalog.route()
   │
   ▼
Authorization + Risk      digital/authorization.py
   │                       CapabilityAuthorizationBroker.evaluate()
   ▼
Execution                 digital/provider.py   execute()  -> existing sandbox
   │
   ▼
Observation               digital/provider.py   observe()
   │
   ▼
Verification              digital/provider.py   verify()
   │
   ▼
Recovery / Retry / Resume digital/contract.py   bounded_retry()
                          digital/runtime.py    checkpoint + audit resume
   │
   ▼
Final Result              digital/runtime.py    DigitalResult
```

`DigitalAgentRuntime.run()` is the single entry point that walks this path.

---

## Capability domains are peers

| Domain | Phase 1 status | Notes |
|---|---|---|
| `filesystem` | active | Root-bound list/read, approval-gated write/transform |
| `os_shell` | active | Allowlisted, root-bound, **read-only**. Not general shell access |
| `web` | active | Bounded public search/read/extract/compare |
| `browser` | active | Bounded open/click/extract over the controlled transport |
| `email` | active | Read-only by default; draft/send stay gated |
| `calendar` | active | Read-only by default; create/update/cancel need human review |
| `github` | active | **One connector among many**, not the product |
| `testing` | active | tests / lint / deterministic metrics |
| `computer` | **reserved** | Declared, unimplemented. Goals that need it fail closed |
| `application` | **reserved** | Declared, unimplemented |
| `documents` | **reserved** | Declared, unimplemented |

Reserved domains are honest. There is no stub executor behind them: the catalog
reports `usable: false`, routing reports the domain as required-but-unavailable,
and the run ends `BLOCKED`. A goal is never reported as done when the capability
that would have done it does not exist.

---

## The adapter contract

Every digital capability implements the same eight-method lifecycle
(`digital/contract.py`):

```python
discover()        -> CapabilityDescriptor      # non-secret self-description
validate_input()  -> InputValidation           # against the registered schema
authorize()       -> CapabilityDecision        # a query, never a decision
execute()         -> CapabilityExecution       # through the existing sandbox
observe()         -> CapabilityObservation     # what actually happened
verify()          -> CapabilityVerification    # evidence required
bounded_retry()   -> CapabilityOutcome         # within the declared budget
audit()           -> None                      # into the hash-chained audit
```

`RegisteredToolCapability` (`digital/provider.py`) is the concrete adapter. It
pairs one tool that is **already** registered in the process `ToolRegistry` with
the sandbox operation that already implements it. Adding a capability means:

1. register the tool in `tool_registry.py` (unchanged authority),
2. add a row to `TOOL_SANDBOX_BINDINGS`,
3. add a `CapabilityDeclaration` in `digital/builtins.py`.

The planner, the runtime, the authorization broker and the sandbox are not
edited. `tests/test_digital_task_routing.py::test_new_capability_routes_without_touching_the_planner`
and `::test_a_new_domain_is_purely_declarative` enforce that.

---

## Safety model — preserved and strengthened

| Guarantee | Where it holds |
|---|---|
| Unknown tools fail closed | `CapabilityCatalog.get()` miss → `BLOCKED`; `CapabilityAuthorizationBroker.evaluate_step()` denies unregistered ids |
| Capabilities cannot self-authorize | `authorize()` only queries `ToolRegistry`; the runtime re-consults the registry immediately before every step |
| High-impact actions need approval | `ConsequenceAwareApprovalPolicy` + registry `ApprovalRequirement`; the runtime returns `REQUIRES_APPROVAL` and executes nothing |
| Credentials never reach model context | `CapabilityRequest` accepts only `credref:` references; `CapabilityDescriptor` refuses credential material; `redact_secret_material()` scrubs all evidence |
| No unrestricted shell | The only shell capability binds to `workspace.shell`, which the registry declares `read_only`; no new execution path was added |
| Destructive actions stay gated | `BLOCKED_INTENT_SIGNALS` makes routing fail closed on mass-destruction phrasing; `destructive.execute` stays a denied capability |
| Payment / billing / deployment stay gated | Those tools are never bound to a digital capability and remain permanently denied in `capability_policy` |
| Verification before success | `verify()` requires observable evidence; `DigitalResultState.VERIFIED` is unreachable otherwise |
| No-op never reported VERIFIED | `observe()` rejects an execution that produced no evidence |
| Checkpoint/resume never replays verified work | `_resumable_steps()` intersects the checkpoint, the plan digest, the authorization digest and the audit's verified steps |
| Audit records stay trustworthy | Hash-chained `execution_audit`; a tampered chain blocks the run and is never extended |
| Grants are narrowed, never widened | `narrow_grants()` intersects caller grants with what the selected capabilities need; `assert_no_escalation()` fails closed |

Two properties are worth calling out because they are new:

**Approval flags are runtime state, not arguments.** The `approved` flag a
connector checks is appended *after* schema validation, from
`CapabilityRequest.approved`, which only the runtime sets from
`explicitly_approved`. A model that puts `"approved": true` in its arguments
gets nothing.

**A lying capability is still blocked.** `LyingCapability` in the test suite
returns `allowed=True` from its own `authorize()`. The run is still blocked,
because the decision comes from the registry.

---

## Routing is data, not code

`CapabilityPlanner` and `DigitalAgentRuntime` contain **zero** domain keywords.
All vocabulary lives in two declarative places:

- `DomainDescriptor.signals` — which domain a goal touches
  (`digital/domains.py`)
- `CapabilityDeclaration.signals` — which operation inside that domain
  (`digital/builtins.py`)

Matching is word-boundary aware, so `repo` does not match `report`, `move` does
not match `remove`, and `file` does not match `profile`.

Selection rules:

1. Match declared domain signals.
2. Within a matched active domain, prefer capabilities whose own signals
   matched.
3. Otherwise fall back to that domain's declared **read-only** default — one
   capability, the narrowest entry point.
4. A matched reserved domain makes the whole goal fail closed.
5. Mass-destruction phrasing fails closed regardless of domain.

Consequence: an ambiguous goal can never select a side effect by accident. A
write capability is only ever selected when the goal explicitly asks for one,
and even then it stays behind its approval gate.

---

## What Phase 1 deliberately does not do

- No computer control, screen interaction, application driving or PDF/document
  content processing. Those domains are declared and blocked.
- No new execution engine, sandbox or shell. `run_safe_operation()` is the only
  execution path.
- No change to the existing `TaskPlan` / `execute_plan()` engineering pipeline.
  The digital layer sits beside it and shares the same registry, policy,
  sandbox and audit primitives.
- No automatic argument extraction. The goal states intent; concrete arguments
  are supplied per capability id. Turning goal text into arguments is later
  work and must not become an authorization path.

---

## Known limitations

- A goal that matches a domain but asks for an action no capability in that
  domain performs (for example "rename these files") is planned against the
  capabilities that *do* exist. The result reports exactly which steps ran and
  verified, so it cannot claim to have done more, but the agent does not yet
  detect the gap and say so.
- `RetryPolicy.backoff_seconds` is declared metadata; the in-process bounded
  retry loop does not sleep.

---

## Running it

```python
from pathlib import Path
from autonomous_agent.digital import build_agent
from autonomous_agent.filesystem_workspace import WorkspaceConnector

root = Path(".").resolve()
agent = build_agent(root=root, connectors={"workspace": WorkspaceConnector(root)})

result = agent.run(
    "list the files in the docs folder",
    root=root,
    audit_path="state/digital_audit.jsonl",
    execution_id="run-1",
    requests={"filesystem:list": {"path": "docs"}},
)
print(result.state, result.reason)
```

See `examples/phase1_digital_agent_demo.py` for a runnable walkthrough of
discovery, routing, approval gating, verification and resume.

---

## Tests

| File | Covers |
|---|---|
| `tests/test_digital_capability_discovery.py` | Domain peers, discovery, reserved-domain honesty, documentation model |
| `tests/test_digital_task_routing.py` | Goal → capability routing, fail-closed behaviour, extensibility |
| `tests/test_digital_authorization.py` | Authorization, self-authorization, approval boundaries, escalation prevention |
| `tests/test_digital_runtime.py` | Execution, verification, no-op detection, retry, checkpoint/resume, audit integrity |
