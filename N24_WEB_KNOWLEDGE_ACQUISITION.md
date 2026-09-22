# N24 — Web Research + Knowledge Acquisition

## Objective

Turn the existing bounded web connector into a structured research pipeline that collects source-backed evidence rather than returning an untraceable answer.

## Capability

```text
research query
    │
    ▼
bounded web search
    │
    ▼
source read / normalization
    │
    ├── extraction with source_ref
    └── multi-source comparison
    │
    ▼
ResearchPacket
    ├── sources
    ├── facts
    ├── provenance
    └── fingerprint
```

The pipeline preserves source references and comparison status such as `verified`, `unavailable`, and `conflicting` instead of silently resolving disagreement.

## Safety

- Reuses the existing web connector's URL, domain, content-type, size, timeout, retry and stale-source bounds.
- Read-only; no web-side writes are introduced.
- Source text remains redacted by the underlying connector.
- Failed source reads fall back to the bounded search evidence rather than inventing content.

## Capability proof

Runnable test:

```bash
pytest -q tests/test_knowledge_acquisition.py
```

The tests demonstrate source collection, provenance, conflict preservation, partial-source failure handling, and empty-result handling.

## Limitations after N24

Still deferred:

- long-term semantic/episodic storage (N26);
- context compaction and cross-task memory (N27);
- credential brokerage/authenticated research (N28);
- prompt-injection defenses for hostile web content (N29);
- autonomous communication workflows (N25).

## Acceptance gate

N24 is VERIFIED only when the knowledge acquisition tests and full CI are green and source provenance remains intact.
