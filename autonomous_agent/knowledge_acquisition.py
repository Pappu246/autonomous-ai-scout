from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .web_domain_connector import WebEvidence, WebResearchConnector


@dataclass(frozen=True)
class ResearchPacket:
    query: str
    sources: tuple[WebEvidence, ...]
    facts: tuple[dict, ...]
    source_count: int
    fingerprint: str


class KnowledgeAcquirer:
    """Structured read-only research pipeline over the bounded web connector."""

    def __init__(self, connector: WebResearchConnector) -> None:
        self.connector = connector

    def collect(self, query: str, *, results: int = 5, fields: Sequence[str] = ()) -> ResearchPacket:
        evidence = self.connector.search(query, results=results)
        read_sources: list[WebEvidence] = []
        seen: set[str] = set()
        for item in evidence:
            if item.url in seen:
                continue
            try:
                full = self.connector.read(item.url)
            except Exception:
                full = item
            read_sources.append(full)
            seen.add(full.url)
        if not read_sources:
            return ResearchPacket(query.strip(), (), (), 0, "")
        facts: list[dict] = []
        if fields:
            for source in read_sources:
                extracted = self.connector.extract(source, fields)
                for field, value in extracted.items():
                    facts.append({"source_ref": source.source_ref, "field": field, **value})
        comparison: dict = {}
        if len(read_sources) >= 2:
            comparison = self.connector.compare(read_sources)
            facts.extend(comparison.get("facts", ()))
        fingerprint = read_sources[0].fingerprint if len(read_sources) == 1 else str(comparison.get("comparison_fingerprint", "")) if len(read_sources) >= 2 else ""
        deduped: list[dict] = []
        seen_facts: set[str] = set()
        for fact in facts:
            key = repr(sorted(fact.items()))
            if key not in seen_facts:
                deduped.append(fact)
                seen_facts.add(key)
        return ResearchPacket(query.strip(), tuple(read_sources), tuple(deduped), len(read_sources), fingerprint)


__all__ = ["KnowledgeAcquirer", "ResearchPacket"]
