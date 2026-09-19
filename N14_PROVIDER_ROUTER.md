# N14 — Production Coding Provider Layer

N14 turns the N13 provider-neutral coding model boundary into a bounded production routing layer.

## Flow

```text
Improvement proposal
  -> CodingProviderRouter
  -> explicitly configured eligible provider
  -> bounded retry/fallback
  -> N13 patch candidate
  -> patch review
  -> temporary-workspace validation
  -> READY_FOR_APPROVAL
  -> N12 GitHub worker
```

## Safety invariants

- Routes are configured explicitly through environment variables.
- API-key values are never stored in provider specs or attempt metadata.
- Missing keys cause a route to be skipped.
- Paid routes are disabled by default and require `allow_paid=True`.
- Fallback occurs only among configured routes.
- Retries are capped at three attempts per provider.
- Provider exceptions and invalid candidates fail closed.
- No provider endpoint/model is silently invented.
- N14 does not branch, commit, open a PR, merge, deploy, or manage billing.

## Environment configuration

A route uses `CODING_PROVIDER_<N>_*` variables, for N=1..8:

```text
CODING_PROVIDER_1_NAME
CODING_PROVIDER_1_ENDPOINT
CODING_PROVIDER_1_MODEL
CODING_PROVIDER_1_API_KEY_ENV
CODING_PROVIDER_1_COST_CLASS       # free|paid|unknown
CODING_PROVIDER_1_PRIORITY
CODING_PROVIDER_1_MAX_ATTEMPTS
CODING_PROVIDER_1_TIMEOUT_SECONDS
```

`API_KEY_ENV` contains the *name* of the environment variable holding the credential, not the credential itself.

N14 deliberately stays endpoint-neutral. Provider-specific defaults should only be added when their official API contract is intentionally supported and tested.
