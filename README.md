# Autonomous AI Scout

A free-first autonomous AI engineering scout designed to run on a schedule, discover legitimate free AI access, evaluate candidates, audit projects, identify improvement opportunities, and produce concise reports.

## Safety contract

This project is intentionally **free-only**:

- It only activates providers whose free access is verified from an official source.
- Community posts are leads, not proof.
- It never bypasses authentication, payment, quotas, rate limits, or access controls.
- It never enables paid billing automatically.
- Expired or uncertain access is not treated as usable.
- Production changes should flow through review/approval before merge or deploy.

## Current foundation

- Typed domain models for models, project findings, opportunities, and reports.
- Official-source verification primitive.
- Free-only model router foundation.
- Persistent JSON state suitable for GitHub Actions.
- Hourly GitHub Actions scheduler (`0 * * * *`).
- Deterministic tests for routing and state handling.
- Provider registry with Gemini enabled and Hugging Face disabled by default until separately verified/implemented.

## Run locally

```bash
python -m pip install -e .
python -m autonomous_agent.main
```

## GitHub Actions

The public repository uses standard GitHub-hosted runners. The workflow is intentionally read/analysis first and only writes its generated state/report back to this repository.

## Roadmap

1. Official-source model discovery across provider docs, releases, and changelogs.
2. Capability/quality/latency benchmark lab.
3. GitHub project inventory and deeper audit engine.
4. Change detection and persistent model/usage history.
5. Opportunity scoring and monetization research.
6. Gmail delivery with secure credentials/OAuth.
7. Sandboxed patch generation and tests, then approval-gated PR creation.
8. Health dashboard and self-improvement loop.
