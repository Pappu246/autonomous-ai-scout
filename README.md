# Autonomous AI Scout

A free-first autonomous AI engineering scout and safety-first foundation for a Universal Digital AI Agent. It discovers the owner's GitHub repositories, builds technical profiles, derives deterministic project-intelligence signals, verifies legitimate free AI access from official provider sources, benchmarks configured free models when credentials are explicitly supplied, scores practical opportunities, tracks changes, and produces bounded reports.

## Safety contract

- Official provider evidence is required before a model is treated as free.
- Community claims are leads only.
- No authentication, payment, quota, rate-limit, or access-control bypassing.
- No automatic paid billing or paid fallback.
- Missing/expired/uncertain access is skipped.
- Benchmarks are opt-in through explicitly configured provider keys.
- Read-only discovery, audits, tests, and deterministic analysis can run autonomously.
- Source writes, merges, production changes, destructive actions, billing, and other irreversible external actions remain approval-gated or permanently denied by the autonomous capability policy.
- GitHub changes are prepared through a separate approval/diff boundary and never merged or deployed automatically.
- Secrets are read only from environment variables/GitHub Actions secrets and are never persisted in scout state.

## Current architecture

### Phase A — Universal repository discovery

- Discovers all repositories owned by the configured GitHub account with pagination.
- Uses authenticated `/user/repos?affiliation=owner` when `GITHUB_TOKEN` is available, so authorized private repositories are included.
- Falls back to the public owner repository endpoint without inventing access.
- Retains archived/fork metadata for registry accuracy while active audit work can exclude them.
- Preserves the last known-good registry when GitHub discovery fails transiently.

### Phase B — Technical project profiles

- Builds bounded, read-only profiles for new or changed repositories.
- Detects ecosystems, root manifests, lockfiles, README/license signals, CI hints, languages, and repository flags.
- Persists profiles separately from the repository registry.
- Refreshes only a bounded number of changed/new projects per cycle.

### Phase C — Universal project intelligence

- Derives deterministic health signals from Phase-B profiles.
- Tracks reproducibility, documentation, licensing, validation, and lifecycle gaps.
- Persists intelligence fingerprints and surfaces meaningful changes only.
- Never turns intelligence findings into unapproved external actions.

### Phase E — Declarative tool registry

- Maintains a central, inspectable catalog of available agent tools and their capabilities.
- Tool registration never grants permission.
- Safe capabilities still require explicit capability grants.
- Source-write, merge, deploy, billing, and destructive capabilities remain blocked by the autonomous policy; approved GitHub changes continue through the dedicated change boundary.

## Project identity

Projects are keyed by their canonical GitHub `owner/name` identity. `Pappu246/solo-ai-v2`, `Pappu246/SOLO-AI`, and `Pappu246/autonomous-ai-scout` are therefore separate projects and cannot be collapsed by display-name similarity.

Future repositories are onboarded automatically when they appear in the owner's repository discovery results; transient discovery failures preserve the previous baseline rather than pretending repositories disappeared.

## Current capabilities

1. Official-source free-access verification and source-change detection.
2. Explicit free-model provider policy with no paid fallback or billing activation.
3. Safe, opt-in model benchmarking.
4. Universal GitHub repository registry with new/changed/removed detection.
5. Bounded technical project profiles.
6. Universal project-intelligence baselines.
7. Opportunity scoring, deduplication, and score-trend history.
8. Approval queues, lifecycle tracking, recovery, and sandbox-aware execution boundaries.
9. Approval-gated GitHub change preparation with protected-branch and patch-digest checks.
10. Declarative safety-aware tool registry.
11. Persistent JSON state and Markdown reporting.
12. Scheduled GitHub Actions validation and scout execution.

## Local run

```bash
python -m pip install -e '.[test]'
pytest -q
python -m autonomous_agent.main
```

## Optional secrets

- `GEMINI_API_KEY` — only a key with a legitimate free route should be supplied.
- `GROQ_API_KEY` — only for explicitly configured free benchmarking.
- `OPENROUTER_API_KEY` — only for the configured free route.
- `SMTP_USERNAME` / `SMTP_APP_PASSWORD` / `REPORT_EMAIL` — optional report delivery.

If these are absent, the agent continues in discovery/audit/report mode and does not fail because of missing credentials.

## Next Universal Digital AI Agent phases

The next architecture layers are deliberately incremental: richer tool adapters, task planning, safe execution across approved tools, persistent memory/learning, browser/API automation, dashboard/observability, and a self-improvement evaluation loop. Each layer must preserve the existing free-only, approval, lifecycle, sandbox, and external-action safety gates.
