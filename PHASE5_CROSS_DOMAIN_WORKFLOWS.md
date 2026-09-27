# Phase 5 — Cross-Domain Workflow Orchestration

This document describes **what is actually implemented** in Phase 5. Where a
protection is partial, layered or absent, it is described as such. Claims here
are backed by code in `autonomous_agent/workflow/` and by the 414 Phase 5 tests
listed in [Test matrix](#test-matrix).

---

## 1. Objective

Phase 5 adds **cross-domain workflow orchestration** and **communication
coordination** to the existing agent: the ability to declare a multi-step
pipeline that spans several already-approved domains (web, documents,
application, filesystem, browser, communication), validate it as a whole, move
data between domains under explicit trust rules, and prepare communication
drafts.

The phase adds **no new external reach**. Every Phase 5 capability runs with
`network=none`, inside the existing sandbox, under the existing audit chain.
Orchestration is a *coordination* layer over capabilities the agent already
had — it does not grant new ones.

### Scope boundary

| | |
|---|---|
| **Does** | declare, validate, sequence, hand off, observe, verify, checkpoint, resume, draft |
| **Does not** | send, deliver, publish, open sockets, spawn processes, evaluate code, reach the network |

**`draft != send`.** There is no autonomous delivery path anywhere in this
layer. See [§5](#5-backend-reality) and [§7](#7-security-model).

---

## 2. Architecture

```
ToolRegistry (5 new tools)
  └─ CapabilityDeclaration (builtins)
       └─ TOOL_SANDBOX_BINDINGS  →  ("workflow", "workflow", <operation>)
            └─ SandboxCapabilityExecutor  →  run_safe_operation("workflow", …)
                 └─ execute_workflow_operation()      [request adapter]
                      └─ BoundedWorkflowConnector      [the decision-maker]
                           ├─ workflow policy          [M1 validation]
                           ├─ WorkflowSession          [state, budget, approvals]
                           ├─ WorkflowReplayProtector  [replay identity]
                           └─ Backend (Mock | Unsupported)
                 └─ WorkflowPostConditionObserver      [independent verification]
                      └─ execution audit + checkpoint
```

No parallel runtime was created. Phase 5 reuses the central
planner → authorization → capability → sandbox → observer → audit pipeline.

### Module inventory (`autonomous_agent/workflow/`, 4,762 lines)

| Module | Lines | Responsibility |
|---|---|---|
| `policy.py` | 1,055 | DAG, trust, budget, approval and operation validation |
| `models.py` | 876 | dataclasses, enums, bounds, redaction, untrusted envelopes |
| `connector.py` | 667 | `BoundedWorkflowConnector` — the enforcement point |
| `integration.py` | 475 | request adapter between the sandbox and the connector |
| `backend.py` | 463 | Base / Mock / Unsupported backends |
| `__init__.py` | 332 | public surface (135 exports) |
| `session.py` | 306 | session state, action budget, snapshot/restore |
| `target.py` | 258 | semantic target resolution |
| `replay.py` | 177 | replay identity and protection |
| `observer.py` | 153 | post-condition observation |

---

## 3. Milestones

### M1 — Models, policy and validation (`2bcb01b`)

* **Workflow models** — `WorkflowPipeline`, `WorkflowStep`, `WorkflowHandoff`,
  `WorkflowArtifact`, `StepExecution`, `CommunicationDraft`, plus enums
  `TrustLevel` (`system`, `user`, `memory`, `tool_result`, `external`),
  `HandoffKind` (`text`, `structured`, `file_path`, `table`, `metadata`,
  `observation`, `digest`), `StepEffect` (`read_only`, `mutating`),
  `VerificationStatus` (`failed`, `accepted`, `observed`, `verified`),
  `WorkflowState`, `SessionState`.
* **DAG validation** — `validate_pipeline()` rejects cycles, self-references,
  missing dependencies, duplicate step and handoff ids, excessive depth and
  excessive step counts. A handoff is only valid when the target
  *transitively depends on* the source, the source *produces* the artifact and
  the target *consumes* it.
* **Action budgets** — every step declares an `action_cost`; the sum may not
  exceed the pipeline's declared `action_budget`, itself capped at
  `MAX_WORKFLOW_ACTION_BUDGET = 100`.
* **Cross-domain handoffs** — `assert_cross_domain_handoff_allowed()` decides
  which domain pairs may exchange data at all.
* **Trust propagation** — `propagate_trust()` walks the DAG in topological
  order; a step's trust is the **lowest** of its own domain trust, every
  dependency's resolved trust and every inbound handoff's trust. Trust never
  upgrades. A handoff from an untrusted domain that does not declare
  `EXTERNAL` trust is refused at validation.
* **Approval policy** — a mutating step in a sensitive sink domain must declare
  `requires_approval=True`; `assert_effect_declared()` refuses a step whose
  operation mutates state while declaring itself read-only.
* **Secret-free checkpoints** — recursive redaction (`redact_structure`,
  `redact_secret`) over parameters, payloads, evidence and history;
  `WorkflowSessionSnapshot` stores **digests only**, never payloads.
* **Operation denylist** — `validate_operation()` refuses execution-primitive
  operation names: `shell`, `system`, `subprocess`, `popen`, `spawn`,
  `execute_script`, `run_script`, `eval`, `cdp`, `debugger`,
  `attach_debugger`, and others.

**Bounds** (all finite, all enforced): 32 steps, 64 handoffs, 6 domains,
8 dependencies/step, 8 execution depth, 10 action cost/step, 100 action budget,
64 KiB artifact payload, 16 KiB draft body, 256-char subject, 20 recipients,
parameter depth 3, redaction depth 32, 100 history entries.

### M2 — Backends, session, targets, replay, connector (`87b81de`)

* **Backends** — `BaseWorkflowBackend` (abstract, every method raises),
  `MockWorkflowBackend` (deterministic simulation), `UnsupportedWorkflowBackend`
  (the default: every operation fails closed).
* **`WorkflowSession`** — bound to exactly one workflow id; tracks completed
  and verified steps, handoff digests, artifact digests, approvals, action
  budget, execution depth, epoch and bounded history. `snapshot()` /
  `restore()` implement checkpoint and resume.
* **Semantic targets** — `WorkflowTargetResolver` resolves steps and handoffs
  by *semantic identifier*, refusing positional/coordinate references
  (`"0"`, `"[1,2]"`), non-strings, empty ids and foreign-workflow references.
  Targets carry the session epoch and go stale when it advances.
* **Replay protection** — `WorkflowReplayProtector` derives step, mutation and
  handoff keys from workflow id, step identity, inbound digests and payload
  digest. A verified step cannot be re-executed; a recorded mutation cannot be
  repeated. Keys survive checkpoint/resume.
* **`BoundedWorkflowConnector`** — the single enforcement point. `handoff()`
  runs a 10-point checklist (declared endpoints, declared edge, verified
  producer, payload bounds, trust floor, hard-sink block, redaction, untrusted
  quarantine, digest, replay record, budget).
* **Deterministic drafts** — `create_draft()` requires an approval for the
  step, bounds body and subject, validates channel against an allowlist
  (`email`, `message`, `calendar`, `comment`) and returns a
  `CommunicationDraft` whose `sent` is constantly `False` and whose
  `delivery_state` is constantly `"draft_only"`.

### M3 — Central-architecture integration (`78eecd3`)

* **Capability registry** — 5 tools registered in `tool_registry.py`
  (catalog in [§4](#4-capability-catalog)). Registry total: 73 tools.
* **Sandbox routing** — `"workflow"` added to `SAFE_OPERATIONS`;
  `_run_workflow()` delegates to the request adapter and always reports
  `network_disabled=True`. It owns no execution primitive and refuses without
  an injected connector and a structured request.
* **Provider bindings** — 5 entries in `TOOL_SANDBOX_BINDINGS` (total 64), each
  mapping to `("workflow", "workflow", <operation>)`.
* **Builtin declarations** — 5 `CapabilityDeclaration`s with routing signals so
  the planner can select them. Declarations total 64, all unique.
* **Workflow observer** — `WorkflowPostConditionObserver` independently
  re-reads workflow state after a call and refuses any result whose evidence
  claims delivery (`sent` truthy, or `delivery_state` outside
  `{draft_only, ""}`), any unknown capability id, and any execution without
  evidence.
* **Runtime / authorization** — capabilities flow through the existing
  `CapabilityAuthorizationBroker`. Grants are **intersected, never unioned**
  (`narrow_grants` + `assert_no_escalation`).
* **Checkpoint / resume** — the runtime writes secret-free checkpoints; an
  interrupted execution yields `RECOVERY_REQUIRED` rather than auto-replaying.
* **Audit** — every Phase 5 tool is `audit=required`; the hash chain stays
  verifiable (`verify_execution_audit`) across successful and refused runs.

### M4 — Adversarial hardening (`c6dae5b`, `7effdfa`, `a2a5faa`)

229 adversarial tests attacking the layer, and **12 real vulnerabilities found
and fixed**. Detail in [§6](#6-m4-security-findings).

---

## 4. Capability catalog

Exactly five capabilities are exposed. Every one is `network=none`,
`authentication=none`, `sandbox=required`, `audit=required`, and has a
**strict** JSON schema (`additionalProperties: false`).

### `workflow:pipeline.plan`
| Property | Value |
|---|---|
| Tool | `workflow.pipeline.plan` |
| Operation | `plan` |
| Risk | `low` |
| Read/write | `read_only` |
| Safe-autonomous | **yes** |
| Approval | `none` |
| Sandbox / audit | required / required |
| Schema | `required: [pipeline]`, strict |
| Verification | validates the pipeline and returns a projection: digest, execution order, domains, trust map, approval-required steps, declared cost and budget. Mutates nothing. |

### `workflow:data.handoff`
| Property | Value |
|---|---|
| Tool | `workflow.data.handoff` |
| Operation | `handoff` |
| Risk | `low` |
| Read/write | `read_only` |
| Safe-autonomous | **yes** |
| Approval | `none` |
| Sandbox / audit | required / required |
| Schema | `required: [source_step, target_step, artifact_key]`, strict |
| Verification | moves one declared artifact across one declared edge after the 10-point checklist; records the recomputed payload digest. Read-only with respect to external state; it consumes budget and is delivered at most once per edge. |

### `workflow:pipeline.execute`
| Property | Value |
|---|---|
| Tool | `workflow.pipeline.execute` |
| Operation | `execute` |
| Risk | **high** |
| Read/write | `controlled_write` |
| Safe-autonomous | **no** |
| Approval | **explicit** |
| Sandbox / audit | required / required |
| Schema | `required: [step_id]`, strict |
| Verification | the backend's acceptance is never sufficient. The connector independently observes the step, re-derives every artifact digest from its payload, requires the observed digests to match, and requires evidence. Only then is the step `VERIFIED`. |

### `communication:meeting.coordinate`
| Property | Value |
|---|---|
| Tool | `communication.meeting.coordinate` |
| Operation | `coordinate` |
| Risk | `low` |
| Read/write | `read_only` |
| Safe-autonomous | **yes** |
| Approval | `none` |
| Sandbox / audit | required / required |
| Schema | no required fields, strict |
| Verification | returns a read-only coordination proposal derived from declared workflow state. Schedules nothing, creates nothing, sends nothing. |

### `communication:draft.prepare`
| Property | Value |
|---|---|
| Tool | `communication.draft.prepare` |
| Operation | `draft` |
| Risk | `medium` |
| Read/write | `controlled_write` |
| Safe-autonomous | **no** |
| Approval | **explicit** |
| Sandbox / audit | required / required |
| Schema | `required: [step_id]`, strict |
| Verification | the observer must match a non-sent draft in connector state; any evidence claiming delivery is refused. The draft's `sent` is constantly `False` and `delivery_state` constantly `"draft_only"`. |

### `email.send` is **not** an autonomous Phase 5 capability

`email.send` exists in the registry as **pre-existing Phase 1–3
infrastructure**. It is:

* `safe_autonomous = False`
* `risk = critical`
* `approval = human_review`
* `read_write_mode = high_risk_write`

Phase 5 registered **no** send capability, declares no capability id containing
`send`, and routes no workflow or communication capability to it. The request
adapter refuses 11 delivery verbs outright — `deliver`, `delivery`, `dispatch`,
`email_send`, `message_send`, `post`, `publish`, `send`, `send_draft`,
`send_email`, `transmit` — with **zero overlap** against the 8-operation
allowlist (`plan`, `handoff`, `execute`, `coordinate`, `draft`,
`observe_workflow`, `observe_step`, `verify`).

---

## 5. Backend reality

Stated plainly, because it matters:

* **A deterministic mock backend exists.** `MockWorkflowBackend` simulates step
  execution from declared inputs. It is deterministic, offline and produces
  reproducible digests. It is what the tests exercise.
* **The default backend fails closed.** `UnsupportedWorkflowBackend` is the
  default when no safe live backend is configured. Every method raises
  `BackendUnavailableError`. `connector.is_live()` reports `False`.
* **No fabricated live success.** The connector never reports a success the
  backend did not report, and never reports `VERIFIED` without independent
  observation whose digests match content it re-derived itself.
* **No unrestricted external network path.** All five tools are
  `network=none`; `run_safe_operation("workflow", …)` always returns
  `network_disabled=True`; the workflow package imports no HTTP client, no
  socket, and no SMTP library.
* **Draft does not mean send.** Drafting is the end of the line. There is no
  send, deliver, dispatch, transmit or publish function anywhere in the
  package, and no method on the public surface whose name begins with `send`.

**There is no production live orchestration.** Phase 5 delivers the
orchestration *contract*, its validation, its safety envelope and a
deterministic backend. Connecting a real backend is future work and is not
claimed here.

---

## 6. M4 security findings

All twelve were found by adversarial testing against M1–M3 code, are real, and
are fixed with **minimum bounded changes**. Each has regression coverage.

| ID | Vulnerability | Impact | Bounded fix | Regression test |
|---|---|---|---|---|
| **M4-1** | A session bound to a workflow could be re-validated with a **different definition** under the same workflow id | Approvals, verified steps and budget carried over to redefined steps — approval laundering | `validate()` pins the validated pipeline digest in session metadata; a mismatching redefinition raises `WorkflowSecurityError` | `test_a_redefined_workflow_cannot_inherit_an_existing_approval` |
| **M4-2** | `WorkflowSession.restore()` trusted `snapshot.action_used` | A tampered or stale checkpoint reset the spent budget to zero — unbounded action laundering across resumes | Restored usage is floored at the work the snapshot itself records (`completed + handoffs`); the limit is clamped to `MAX_WORKFLOW_ACTION_BUDGET` | `test_resume_cannot_reset_the_spent_action_budget`, `test_resume_cannot_widen_the_action_budget_limit` |
| **M4-3** | `redact_structure()` recursed without a depth bound | A deeply nested payload raised `RecursionError` out of the connector as a non-workflow error | Depth-bounded walk (`MAX_REDACTION_DEPTH = 32`) replacing deeper content with an inert marker | `test_recursive_redaction_is_depth_bounded` |
| **M4-4** | A completed handoff edge could be repeated with **modified** content (replay identity keyed on payload) | The input a destination's approval was granted against could be silently swapped; repeats burned budget | One delivery per declared edge, recorded in session state so it survives checkpoint/resume; a repeat raises `WorkflowReplayError` | `test_a_delivered_handoff_cannot_be_repeated_with_modified_content` |
| **M4-5** | `wrap_untrusted_handoff_content()` embedded attacker text between literal delimiters | Untrusted content carrying the banner could **close its own quarantine block** and present following text as trusted | Banner occurrences inside content are defanged before wrapping; exactly one real BEGIN and one real END remain | `test_untrusted_content_cannot_close_its_own_quarantine_block` |
| **M4-6** | `grant_approval(approver=…)` used `str(approver)` | `None` became the approver name `"None"`, passing the "must name the approving human" gate | `isinstance(approver, str)` is required before stripping | `test_approval_cannot_name_a_non_string_approver` |
| **M4-7** | Backend-claimed artifact digests were never re-derived from the payload | A lying or compromised backend could reach `VERIFIED` while delivering content that differed from what it attested | `_verify_execution()` recomputes `artifact_digest(payload)` and refuses any mismatch | `test_an_artifact_that_does_not_hash_to_its_claimed_digest_is_not_verified` |
| **M4-8** | Handoff replay identity used the backend-supplied `sha256` | A backend-controlled digest could defeat replay detection and poison session state | The digest is recomputed from the payload; a contradicted claim raises `WorkflowSecurityError` | `test_a_transfer_digest_that_contradicts_the_payload_is_refused` |
| **M4-9** | A step declaring no outputs was exempt from the evidence check | Zero evidence could still yield `VERIFIED` | The `and declared` exemption is removed — no evidence, no verification | `test_a_step_with_no_declared_output_still_needs_evidence` |
| **M4-10** | The session budget limit could be widened after validation (tampered checkpoint or direct attribute write) | More actions than the definition allowed | `_require_pipeline()` re-asserts `session.limit <= pipeline.action_budget` on every entry | `test_widening_the_session_budget_after_validation_is_detected` |
| **M4-11** | Draft recipients were `str()`-coerced and never screened | A non-string became the recipient `"None"`; CR/LF survived into a draft — the shape of a header injection for whatever a human later sends it with | Recipients must be strings free of control characters; subjects have control characters collapsed | `test_hostile_recipient_lists_are_refused`, `test_a_draft_subject_cannot_smuggle_a_header_break` |
| **M4-12** | `_bind_pipeline()` only validated a supplied pipeline when the connector had none | A later request carrying a **different** definition under the same workflow id was silently ignored rather than refused | The adapter always revalidates; the M4-1 digest pin makes an identical resend idempotent and a substitution fail closed | `test_a_request_may_not_swap_the_bound_pipeline` |

### Documented as correct, not changed

* **Tool-name canonicalisation** (Phase 1–3) resolves padded/uppercase names to
  the *identical* spec with *identical* gates; an unapproved call with a padded
  name is still denied. Verified by
  `test_tool_name_canonicalisation_never_loosens_a_requirement`.
* **Approver identity is retained** in session history (`approval_by:<name>`)
  deliberately, for auditability. It is redacted for secret material but not
  removed.
* **The pre-existing `email.send`** stays in the registry, CRITICAL and
  human-review-gated, unreachable from every Phase 5 capability.

### Preserved invariants (verified after every fix)

* Untrusted content stays `EXTERNAL` and visibly quarantined across four
  domain hops.
* A payload-declared approval, trust label, role, grant or policy override
  grants exactly nothing; it is recorded as an `authority_claim:*` signal.
* Secrets are redacted from parameters, handoffs, artifacts, observations,
  drafts, snapshots, replay keys, history, metadata and error messages.
* Mutating steps require an explicit human approval, scoped to one step.
* Every delivery verb is refused; drafts remain `draft_only`.
* Phase 1–3 and M1–M3 behaviour is unchanged (full suite green throughout).

---

## 7. Security model

### Trust

Trust flows downward only. A step's trust is the lowest of its domain trust,
its dependencies' trust and its inbound handoffs' trust. A handoff from an
untrusted domain must declare `EXTERNAL` trust or validation refuses it. A
backend cannot hand back content more trusted than it received
(`_enforce_trust_floor`), and untrusted payloads are re-wrapped in a quarantine
envelope if a backend strips it.

### Authority

Authority comes from the session and the runtime, never from data:

* `approved` is **runtime state appended after schema validation**. No
  registered schema exposes it, so a model cannot express it.
* Mutating operations (`execute`, `draft`) additionally require a
  **named-human approval** recorded on the session for that specific step.
* Approvals are per-step, per-session, pinned to one pipeline digest, and do
  not flow upstream→downstream or across executions.

### Hard sinks

Untrusted content may never be handed to `OS_SHELL` or `COMPUTER`. This is
enforced at validation and again at handoff, and holds even when the consuming
step is approved.

### Verification

`VERIFIED` requires: the backend accepted the step **and** an independent
observation succeeded **and** produced artifacts match the declared outputs
**and** every artifact hashes to its claimed digest **and** observed digests
match **and** evidence exists. Failing any one yields `FAILED`/`ACCEPTED`/
`OBSERVED`, never `VERIFIED`.

### Replay and budget

Verified steps, recorded mutations and delivered handoff edges cannot be
repeated, before or after a resume. The action budget is bounded by the
validated definition, re-checked on every entry, floored on restore, and
consumed by failed attempts as well as successful ones.

### Static review

Scanned 10 Phase 5 modules (4,762 lines) and Phase 5's 141 added lines in
shared modules for: `subprocess`, `os.system`, `shell=True`, `eval`, `exec`,
`compile`, `__import__`, `pickle`, `marshal`, `socket`, `smtplib`, `urllib`,
`requests`, `httpx`, `http.client`, `ftplib`, `paramiko`, `webbrowser`,
`ctypes`, `os.popen`, `os.fork`, `os.spawn`, `Popen`, CDP/devtools, JavaScript
evaluation, and hidden send/deliver/dispatch functions.

**Result: no call sites.** The only textual matches are:

| Match | Location | Why it is safe |
|---|---|---|
| `"subprocess"`, `"cdp"`, `"debugger"`, `"execute_script"`, `"shell"`, `"system"`, `"popen"`, `"spawn"` | `policy.py` denylist tuple | String literals in the list of **refused** operation names; `validate_operation()` raises for each |
| `socket` | `backend.py` module docstring | Prose stating the module never opens one |
| `compile(` | `models.py`, `policy.py`, `target.py` | Exclusively `re.compile(` for static patterns; asserted by test |
| `def delivery_state` | `models.py` | A read-only property that constantly returns `"draft_only"` |

---

## 8. Test matrix

| Suite | Tests | Purpose |
|---|---|---|
| `tests/test_workflow_policy.py` | 71 | M1 models, DAG, trust, budget, approval policy |
| `tests/test_workflow_agent.py` | 58 | M2 backends, session, targets, replay, connector, drafts |
| `tests/test_workflow_integration.py` | 56 | M3 registry, sandbox, provider, observer, runtime, audit |
| `tests/test_workflow_safety.py` | **153** | M4 adversarial — sections 3–18 |
| `tests/test_communication_safety.py` | **76** | M4 adversarial — communication + central runtime |
| **Phase 5 total** | **414** | |

Final results (recorded verbatim):

```
pytest -q                                             1980 passed, 6 skipped
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q            1980 passed, 6 skipped
pytest -q test_workflow_safety.py test_communication_safety.py
                                                       229 passed
python3 -m compileall autonomous_agent tests          exit 0
```

Phase 1–4 regression suites (`test_phase4_safety.py`,
`test_application_safety.py`, `test_documents_safety.py`, `test_sandbox.py`,
`test_tool_registry_complete.py`, `test_digital_runtime.py`,
`test_digital_capability_discovery.py`): **245 passed**.

---

## 9. Domain and capability audit

| Check | Result |
|---|---|
| Registry tools | 73 |
| Sandbox bindings | 64 |
| Capability declarations | 64, all unique — **no duplicates** |
| Duplicate tool names | none |
| Capability ids changed | none (Phase 5 adds only) |
| New domains activated | `workflow` and `communication` only, both intended |
| New unapproved capability | none |
| Autonomous email sending | none |
| Phase 6 functionality | none |
| Unrelated architecture changes | none |

Phase 5 diff against the Phase 4 merge base (`4d41213`): **23 files,
+10,822 / −4**. The four deleted lines are the two-line adjustments in
`tests/test_sandbox.py` and `tests/test_digital_capability_discovery.py` that
account for the new `workflow` sandbox operation and the five new tools.

---

## 10. Limitations

Stated honestly, so nobody mistakes the envelope for the product:

1. **No live backend.** Only the deterministic mock exists. The default
   backend fails closed. Phase 5 does not perform real cross-domain work
   against real systems.
2. **The mock is a simulation.** Its evidence is derived from declared inputs,
   not from observing real external state. The verification *contract* is
   real; the observed world is simulated.
3. **A fully malicious backend is partially outside the threat model.** The
   connector now re-derives every digest it can compute from payloads it
   holds, and refuses contradictions. But the "independent" observation is
   provided by the same backend object, so a backend that lies *consistently*
   about state the connector cannot compute is not detectable at this layer.
   Backends are trusted infrastructure, not model-controlled input.
4. **Delivery verbs are refused at the backend and the request adapter, not by
   `validate_operation()`.** A step declaring `operation="send"` passes M1
   policy validation; it is then refused by `MockWorkflowBackend`
   (`WorkflowSecurityError: autonomous delivery is not implemented`) and by
   `UnsupportedWorkflowBackend` (`BackendUnavailableError`), and the verb is
   refused outright at the adapter. It is **not exploitable** — verified
   against all six delivery verbs and both backends — but the earliest gate
   does not itself reject it. Adding delivery verbs to the M1 operation
   denylist would be a defence-in-depth improvement.
5. **`communication:meeting.coordinate` proposes only.** It reads declared
   workflow state; it does not touch a calendar.
6. **Approver identity is caller-supplied.** The runtime `approved` flag is the
   real gate and is model-unreachable; the `approver` *name* recorded for audit
   comes from the tool arguments. Attribution is therefore only as trustworthy
   as the caller supplying it.
7. **Budget semantics are per-session.** Budgets bound actions within one
   workflow session; they are not a global rate limit.

---

## 11. Persistence chain

| Milestone | SHA |
|---|---|
| Phase 4 merge (base) | `4d4121365cc788f9180a8998eb7de3415677ce3b` |
| Phase 5 M1 | `2bcb01b822e3e969ec3ae62f4bb0645b6b382bbe` |
| Phase 5 M2 | `87b81deb47e0bf86f3dabf316238de739eb78318` |
| Phase 5 M3 | `78eecd3db71f69173be3d34437507575b7fe110c` |
| Phase 5 M4 (first persistence) | `c6dae5be97e24f9c0a8745e977fedc80e9c15a13` |
| Phase 5 M4 (final) | `a2a5faa742b4a2a7c64799ac83fff099623d74ca` |

Branch: `arena/01a0df92-autonomous-ai-scout`.
