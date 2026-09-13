# N7 — Controlled Browser Connector

N7 adds a bounded browser abstraction for browser-driven digital work without exposing arbitrary browser execution.

## Safety contract

- HTTPS-only navigation.
- Every host must be explicitly allowlisted.
- URL userinfo and non-default ports are rejected.
- `file:`, `javascript:`, `data:`, and FTP URLs are rejected.
- Navigation, click, and extraction are routed through an injected transport; this module does not launch a browser itself.
- Selectors, URLs, links, title and extracted text are size-bounded.
- No arbitrary JavaScript execution, shell execution, downloads, credential handling, or unrestricted navigation.
- Real browser transport can be attached later through the existing Tool Registry → Capability Policy → Safe Executor → Sandbox path.

## Initial operations

- `browser.open`
- `browser.click`
- `browser.extract`

The connector is deliberately transport-agnostic so deterministic CI can use an injected fake transport and a future Playwright/WebDriver adapter can be added without creating a second authorization boundary.
