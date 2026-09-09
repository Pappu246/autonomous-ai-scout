# Autonomous AI Scout

A free-first autonomous AI engineering scout. It runs on a schedule, verifies legitimate free AI access from official provider sources, benchmarks configured free models when credentials are explicitly supplied, audits the owner's public GitHub projects, scores practical opportunities, tracks source changes, and can send meaningful reports by Gmail SMTP.

## Safety contract

- Official provider evidence is required before a model is treated as free.
- Community claims are leads only.
- No authentication, payment, quota, rate-limit, or access-control bypassing.
- No automatic paid billing or paid fallback.
- Missing/expired/uncertain access is skipped.
- Benchmarks are opt-in through explicitly configured provider keys.
- Production code changes are not automatically merged or deployed.
- Secrets are read only from environment variables/GitHub Actions secrets and are never persisted in scout state.

## Current capabilities

1. Official-source free-access verification and source-change detection.
2. Gemini and Groq registry with explicit model allowlists; Hugging Face remains disabled until its billing/free-credit semantics are explicitly handled.
3. Safe Gemini benchmark with no paid retry.
4. GitHub owner repository inventory and engineering findings.
5. Opportunity/monetization scoring.
6. Persistent JSON state and Markdown report.
7. Hourly GitHub Actions execution.
8. Optional Gmail SMTP delivery only when the required secrets exist.

## Local run

```bash
python -m pip install -e '.[test]'
pytest -q
python -m autonomous_agent.main
```

## GitHub Actions secrets for email/benchmarks

Optional secrets:

- `GEMINI_API_KEY` — only a key with a legitimate free route should be supplied.
- `SMTP_USERNAME` — Gmail address used for sending.
- `SMTP_APP_PASSWORD` — Gmail app password, not the normal account password.
- `REPORT_EMAIL` — destination address.

If these are absent, the agent continues in discovery/audit/report mode and does not fail because of missing credentials.

## Roadmap

- broader official changelog/release discovery
- richer multi-provider benchmark adapters
- project dependency/security analysis
- opportunity deduplication and trend history
- sandboxed patch generation and tests
- approval-gated PR creation
- dashboard and self-improvement evaluation loop
