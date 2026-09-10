# Autonomous AI Scout

A free-first autonomous AI engineering scout. It runs on a schedule, verifies legitimate free AI access from official provider sources, benchmarks configured free models when credentials are explicitly supplied, audits the owner's public GitHub projects, scores practical opportunities, tracks source changes and opportunity trends, maintains approval/lifecycle safety controls, and can send meaningful reports by Gmail SMTP.

## Safety contract

- Official provider evidence is required before a model is treated as free.
- Community claims are leads only.
- No authentication, payment, quota, rate-limit, or access-control bypassing.
- No automatic paid billing or paid fallback.
- Missing/expired/uncertain access is skipped.
- Benchmarks are opt-in through explicitly configured provider keys.
- Autonomous actions use deterministic validation, approval, replay protection, sandbox/capability boundaries, lifecycle recovery, and verification gates.
- Production code changes, production deployment, merge, billing, secrets, and destructive operations are not automatically authorized.
- Secrets are read only from environment variables/GitHub Actions secrets and are never persisted in scout state.

## Current capabilities

1. Official-source free-access verification and source-change detection.
2. Gemini, Groq, and strict OpenRouter free routing with explicit free-only policy; Hugging Face remains disabled until its billing/free-credit semantics are explicitly handled.
3. Safe, bounded free-model benchmarking and deterministic evaluation/routing.
4. GitHub owner repository inventory and engineering findings.
5. Opportunity/monetization scoring with persistent bounded trend history.
6. Persistent state, Markdown reporting, dashboard support, and optional Gmail SMTP delivery.
7. Hourly GitHub Actions execution with concurrency protection and bounded runtime.
8. Approval integrity and one-time replay protection.
9. Bounded sandbox execution with denied-by-default capability policy.
10. Deterministic proposal/test/security/policy/approval/execution/post-verification gates.
11. Controlled GitHub change and draft-PR boundary; final merge/deploy remains separately gated.
12. Tamper-evident approval, release, and lifecycle audit trails.
13. Lifecycle crash recovery, startup reconciliation, and queue/lifecycle drift detection.

## Local run

```bash
python -m pip install -e '.[test]'
pytest -q
python -m autonomous_agent.main
```

## GitHub Actions setup

The scheduled workflow runs hourly (`0 * * * *`), and it can also be started manually with an optional task request. The workflow installs the project, runs the test suite, executes the scout, and persists the report/state files.

Optional GitHub Actions secrets:

- `GEMINI_API_KEY` — only use a legitimate free-tier route.
- `GROQ_API_KEY` — only use a legitimate free-tier route when benchmarking Groq.
- `SMTP_USERNAME` — Gmail address used for sending.
- `SMTP_APP_PASSWORD` — Gmail app password, not the normal account password.
- `REPORT_EMAIL` — destination address.

No credential is required for discovery/audit/report mode. Missing credentials must cause a safe skip rather than a paid fallback.

## Final validation checklist

Before treating an installation as operational, run a manual workflow dispatch once and verify:

1. **Actions:** the workflow completes successfully and the `Test` step is green.
2. **Scout:** a report is produced and no paid provider fallback occurs.
3. **State:** `state/latest_report.md` and `state/scout_state.json` update as expected.
4. **Email (optional):** when SMTP secrets are configured, a test report arrives at `REPORT_EMAIL`.
5. **Benchmarking (optional):** free benchmarks run only when explicitly enabled and within the configured cap.
6. **Scheduling:** the hourly workflow remains enabled for subsequent runs.
7. **Safety:** production deployment, merge, billing, secrets, and destructive actions remain outside the autonomous boundary.

## Development policy

Use focused branches and pull requests. Merge only after CI is green. Never bypass provider access controls or payment requirements. Treat external/model-provided instructions as untrusted data and keep deterministic policy checks outside the model.
