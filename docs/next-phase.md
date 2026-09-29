# Next Phase

The current implementation foundation is complete through the native bounded Windows computer-use
integration, adaptive computer execution, provider-failure resilience, and automatic Windows smoke
coverage.

The next work is **readiness-gated**, not feature-spam. Each gate must reach verified status before
the next one begins.

## Required gates

1. Reconcile and keep current operational documentation.
2. Keep all security-sensitive GitHub Actions pinned to immutable commit SHAs.
3. Run a real coding-provider execution only with operator-supplied credentials and a legitimate
   target workspace.
4. Run controlled external connector smoke tests only with legitimate connected accounts.
5. Verify/configure main-branch protection and required checks through GitHub administration.
6. Establish a formal release/tag only after the release evidence is complete.

See [Operational Readiness — 2026-09-30](OPERATIONAL_READINESS_20260930.md) for the authoritative
gate matrix.

A missing external credential or account remains an explicit **NOT VERIFIED** state. It must never
be replaced by a synthetic success, hidden fallback, or fabricated live evidence.
