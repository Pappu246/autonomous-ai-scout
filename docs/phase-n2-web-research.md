# Phase N2 — Web Research Execution Boundary

N2 exposes four read-only public-web capabilities: `web.search`, `web.read`, `web.extract`, and `web.compare`.

## Execution boundary

`Task Orchestrator → Task Planner → Tool Registry → Capability Policy → existing Safe Executor → existing Sandbox → Verification → CrossProjectMemory → existing Audit`

The orchestrator has no network execution authority. The existing executor re-authorizes every step through the Tool Registry and maps `WEB_RESEARCH` only to the explicitly allow-listed `web_research` sandbox operation. The sandbox accepts web access only through an injected approved `WebResearchConnector` transport.

## Web safety limits

- HTTP(S) public URLs only; embedded credentials and local/private hosts are rejected.
- Connector domain scope and final redirect URL are validated.
- Supported content types are limited to HTML, plain text, and JSON.
- Query, result count, field count, source count, response size, text size, timeout, retries, and connector concurrency are bounded.
- Search results are normalized and deduplicated; evidence receives deterministic fingerprints and stale markers.
- Extraction is deterministic and source-linked; comparison preserves source evidence and labels contradictory field values as `conflicting` rather than inventing a resolution.
- Secrets and credential-like values are redacted before evidence/output persistence.
- Cross-project memory stores evidence and execution observations only; it cannot grant authority.
- Audit records remain append-only and hash-verified; interrupted executions are never automatically replayed.
- `network.fetch` remains permanently denied by autonomous capability policy; approval cannot override that denial.
- No web-write, login, CAPTCHA, paywall bypass, credential harvesting, subprocess, filesystem, or unrestricted browser authority is provided by N2.

## Verification

Repository CI covers the complete test suite and controlled transport-based execution tests. Live external-web execution through the repository boundary is not part of CI unless an approved runtime transport is explicitly supplied; no credentialed or access-control-bypassing transport is permitted.
