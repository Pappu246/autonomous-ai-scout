# N8 — Cross-Domain Workflow Engine

N8 is the orchestration layer for multi-step digital tasks. It composes already-registered tools without creating a second permission or execution authority.

## Contract

- A workflow is a bounded ordered sequence of registered tool names.
- Every step is authorized independently through the existing Tool Registry and Capability Policy.
- Workflow execution never weakens a tool's approval, sandbox, audit, network, authentication, or read/write requirements.
- Cross-domain composition is allowed for safe read-only tools such as web, browser, files, Gmail reads, calendar reads, inspection, tests, metrics, and benchmarks.
- High-risk writes and human-review actions remain gated by the underlying tool contract.
- Maximum step count and workflow name/input sizes are bounded.
- A failed step stops the workflow; no automatic replay is performed.

## Example flows

`browser.open → browser.extract → filesystem.write` (write remains explicitly approved)

`web.search → web.read → email.draft` (draft remains explicitly approved)

`calendar.list → filesystem.write` (write remains explicitly approved)
