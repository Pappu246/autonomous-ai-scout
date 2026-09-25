# Phase 3 — Advanced Bounded Browser Agent

## Overview

Phase 3 activates an **advanced, bounded browser agent** on top of the universal
digital agent foundation established in Phase 1 and the bounded Windows computer
control of Phase 2. It adds stateful, semantic, approval-gated browser
automation with strict safety boundaries.

The canonical execution lifecycle remains authoritative and unchanged:

```text
User Goal
   → Intent Understanding
   → Task Planning
   → Capability / Tool Selection
   → Authorization + Risk Evaluation
   → Execution
   → Observation
   → Verification
   → Recovery / Retry / Resume
   → Final Result
```

No second runtime, authorization broker, sandbox or audit trail is introduced.
The browser agent reuses the existing `ToolRegistry`, `ConsequenceAwareApprovalPolicy`,
`PromptInjectionGuard`, sandbox, audit, checkpoint/resume and retry/budget
machinery. It adds *routing, lifecycle and verification*, never new execution
power.

---

## 12 Canonical Browser Capabilities

All 12 are registered in the central `ToolRegistry` under `Capability.BROWSER`
and bound to the existing sandbox `browser` slot.

| # | Capability ID | Tool Name | Mode | Safe Autonomous | Approval |
|---|---|---|---|---|---|
| 1 | `browser:session.open` | `browser.session.open` | read_only | Yes | None |
| 2 | `browser:navigate` | `browser.navigate` | read_only | Yes | None |
| 3 | `browser:back` | `browser.back` | read_only | Yes | None |
| 4 | `browser:forward` | `browser.forward` | read_only | Yes | None |
| 5 | `browser:reload` | `browser.reload` | read_only | Yes | None |
| 6 | `browser:page.observe` | `browser.page.observe` | read_only | Yes | None |
| 7 | `browser:element.find` | `browser.element.find` | read_only | Yes | None |
| 8 | `browser:element.click` | `browser.element.click` | controlled_write | No | Explicit |
| 9 | `browser:element.type` | `browser.element.type` | controlled_write | No | Explicit |
| 10 | `browser:element.select` | `browser.element.select` | controlled_write | No | Explicit |
| 11 | `browser:download.start` | `browser.download.start` | controlled_write | No | Explicit |
| 12 | `browser:file.extract` | `browser.file.extract` | read_only | Yes | None |

### Legacy compatibility preserved

The three legacy capabilities remain registered and unchanged:

- `browser.open`
- `browser.click`
- `browser.extract`

They are re-routed through the same bounded connector so a single safe boundary
serves both the legacy and the advanced surface.

---

## Architecture

```text
autonomous_agent/browser/
├── __init__.py      # public surface
├── models.py        # exceptions, bounds, secret redaction, data shapes
├── policy.py        # navigation/redirect/download/host/credential validation
├── session.py       # stateful bounded session + snapshot/restore
├── target.py        # semantic target resolution (stale/foreign fail closed)
├── backend.py       # Base / Mock / Unsupported backends (no CDP)
├── connector.py     # BoundedBrowserConnector: the 12 + legacy capabilities
├── observer.py      # (reserved) post-condition helpers
├── replay.py        # replay protection for resume
└── workflow.py      # bounded multi-step observe→target→execute→verify loop
```

The connector is the only object the sandbox talks to. It composes the backend,
the bounded session, semantic target resolution, replay protection, the
prompt-injection guard and the navigation/download policy.

---

## Security Boundaries

### Navigation
- HTTPS only; explicit host allowlist required (never "allow all").
- Every redirect is re-validated against the same policy; redirect depth is bounded.
- Rejected schemes: `file:`, `javascript:`, `data:`, `blob:`, `about:`, `ftp:`,
  `ws:`, `wss:`, `mailto:`, and more.
- Loopback (`127.0.0.0/8`, `::1`, `localhost`), private (`10/8`, `172.16/12`,
  `192.168/16`), link-local (`169.254/16`, `fe80::/10`) and cloud-metadata
  (`169.254.169.254`) addresses are blocked even if mistakenly allowlisted.
- Embedded credentials (`user:pass@host`) and unsafe ports (anything other than
  443) are rejected.
- Response/evidence size is bounded.

### Semantic interaction
- Targets resolve by ARIA role, accessible name, visible text, or a stable selector.
- A target is bound to the page epoch it was resolved against.
- Stale targets (page navigated) and foreign targets (never on this page) fail closed.
- There is **no** blind coordinate fallback.
- Password fields, autocomplete credential hints and secret-named controls are blocked.

### Credentials
- Password/credential fields cannot be interacted with.
- Credential material (`api_key=`, `token:`, `Bearer …`) is refused as typed text.
- Secrets are redacted from all evidence so they never enter model context or audit.

### Consequential actions
- Payment, checkout, account deletion, account/security changes and message/email
  sending remain approval-gated. Mutating capabilities are `CONTROLLED_WRITE`,
  `safe_autonomous=False`, `ApprovalRequirement.EXPLICIT`; the connector also
  refuses a consequential action whose visible label implies high impact unless
  explicitly approved.

### Prompt injection
- All page/DOM/downloaded content is untrusted data (`TrustLevel.EXTERNAL`).
- It is wrapped by `PromptInjectionGuard` and flagged; it can never become
  trusted instructions or trigger an action on its own.

### Downloads
- HTTPS + host validation + redirect validation.
- Filenames are sanitized and confined to the workspace root (path-traversal defense).
- Size limit, safe overwrite, SHA-256 checksum, and independent verification.

### Replay
- A checkpoint/resume never blindly repeats a completed mutation
  (`BrowserReplayProtector`).

### Verification
- Input dispatch is **not** verification. `BrowserPostConditionObserver`
  independently re-observes the page (or re-reads a download and checks its
  checksum) before any step is reported VERIFIED.

### Workflow bounds
- Bounded action count, navigation history, redirect depth, response size,
  download size and step count. No infinite loops.

---

## Deterministic Backend & Live-Browser Status

- CI and tests use the deterministic `MockBrowserBackend`: no network, no real
  browser, no scripts. Every capability is exercised deterministically.
- `UnsupportedBrowserBackend` is the default when no safe backend is configured;
  every operation **fails closed**. There is deliberately **no** raw CDP /
  debugger backend — that would be arbitrary code execution and is out of scope.
- A real deployment injects a narrowly-scoped, audited backend that implements
  the same structured surface and nothing more. Live-browser success is never
  fabricated; unsupported environments fail closed and are documented as such.

---

## Test Coverage

| File | Focus |
|---|---|
| `tests/test_browser_policy.py` | navigation, redirect, host, download, filename, credential policy |
| `tests/test_browser_agent.py` | backend, session, targets, connector, legacy compat, fail-closed |
| `tests/test_browser_capabilities.py` | registration, sandbox bindings, catalog, end-to-end verification, approval boundaries |
| `tests/test_browser_safety.py` | adversarial: dangerous schemes, allowlist bypass, loopback/metadata, credentials, replay, false VERIFIED, approval bypass, no JS/CDP, workflow bounds, injection |

---

## Reserved Domains (unchanged)

- `application` — **RESERVED** (no application adapters implemented).
- `documents` — **RESERVED** (no document parsing; `browser.file.extract` only
  performs a bounded, dependency-free read of a previously downloaded workspace
  file — it does not parse PDFs/office documents).

## Phase 4

Phase 4 has **not** been started. No application or document capability code
exists in this phase.
