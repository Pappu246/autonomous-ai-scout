# Action Boundary

The scout separates planning from execution.

Read-only inspection, discovery, audits, and tests may run automatically. Any task or generated step that mentions source modification, deletion, credentials/secrets, billing/payment, merge, release, deployment, production, or destructive operations is treated as approval-required.

The action boundary is deterministic: an LLM cannot turn a sensitive action into an automatically executable action by returning `requires_approval=false`.
