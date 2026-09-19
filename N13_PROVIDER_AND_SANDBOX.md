# N13 Provider + Sandbox Validation

N13 now has concrete runtime adapters:

- `OpenAICompatibleCodingModel`: provider-neutral HTTP adapter using an OpenAI-compatible chat-completions contract. API keys are read only from the configured environment variable.
- `LocalSandboxTestRunner`: copies the repository into a temporary directory, applies candidate file contents there, and runs a small non-shell allowlist of test commands.

Safety:
- Missing API key returns no candidate.
- Secrets/private keys are redacted from model context.
- Candidate patches still pass the existing patch-review gate.
- Test commands reject shell chaining, redirection, command substitution, and Python `-c`.
- Sandbox execution has a hard timeout.
- No source-control mutation occurs during generation or validation.
- N12 remains the approved remote mutation boundary.
