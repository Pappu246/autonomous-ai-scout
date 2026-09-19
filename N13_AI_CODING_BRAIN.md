# N13 — Real AI Coding Brain

N13 connects improvement proposals to an injected coding model and repository context, then feeds the generated candidate into the existing bounded self-improvement loop.

## Flow

`finding -> proposal -> repository inspection -> model patch generation -> patch review -> validation -> bounded revision -> READY_FOR_APPROVAL`

The brain is deliberately side-effect free. It does not write files, create branches, commit, open PRs, merge, deploy, or manage credentials.

## Boundaries

- Repository context is read-only and bounded to 30 files and 40,000 characters per file.
- Secrets and private-key material are redacted before model context is constructed.
- Patch safety remains enforced by `review_patch`.
- Validation is injected; the brain never assumes a generated patch works.
- Revision count is capped by `SelfImprovementLoop`.
- A successful result still requires human approval before N12 GitHub execution.

## Provider integration

The `CodingModel`, `RepositoryInspector`, and `TestRunner` protocols keep provider-specific networking and credentials outside the policy core. An OpenAI-compatible, Gemini, Groq, local model, or other implementation can be supplied without weakening the safety boundary.

## Next step

N14 can connect an actual provider adapter and a sandboxed test runner to the N13 contracts. N12 remains the downstream approved-change execution boundary.
