from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .cross_project_memory import CrossProjectMemory, MemoryEvent, _safe_text

MAX_QUERY_TOKENS = 32
MAX_RECALL = 10
MIN_SCORE = 0.15
_TOKEN = re.compile(r"[a-z0-9_]{2,}")


@dataclass(frozen=True)
class MemoryMatch:
    score: float
    project: str
    kind: str
    outcome: str
    fingerprint: str
    data: Mapping[str, Any]


def _tokens(value: object) -> frozenset[str]:
    return frozenset(_TOKEN.findall(_safe_text(value).lower())[:MAX_QUERY_TOKENS])


def _similarity(query: frozenset[str], value: frozenset[str]) -> float:
    if not query or not value:
        return 0.0
    intersection = len(query & value)
    if not intersection:
        return 0.0
    return intersection / math.sqrt(len(query) * len(value))


class PersistentMemory:
    """Episodic persistence plus deterministic bounded retrieval over the existing memory log."""

    def __init__(self, path: str | Path, *, max_recall: int = MAX_RECALL) -> None:
        self.store = CrossProjectMemory(Path(path))
        self.max_recall = max(1, min(int(max_recall), MAX_RECALL))

    def record_episode(
        self,
        project: str,
        task: str,
        *,
        outcome: str,
        summary: str = "",
        kind: str = "episode",
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        safe_task = _safe_text(task)
        safe_summary = _safe_text(summary or task)
        payload = {
            "task": safe_task,
            "summary": safe_summary,
            "task_tokens": sorted(_tokens(safe_task)),
            "summary_tokens": sorted(_tokens(safe_summary)),
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        fingerprint = f"episode:{task}"
        return self.store.record(
            MemoryEvent(project, kind, fingerprint, outcome, payload)
        )

    def record_fact(
        self,
        project: str,
        subject: str,
        value: str,
        *,
        source: str = "",
    ) -> bool:
        payload = {
            "subject": _safe_text(subject),
            "value": _safe_text(value),
            "source": _safe_text(source),
            "tokens": sorted(_tokens(f"{subject} {value}")),
        }
        return self.store.record(
            MemoryEvent(
                project,
                "fact",
                f"fact:{project}:{subject}:{value}",
                "stored",
                payload,
            )
        )

    def recall(
        self,
        project: str,
        query: str,
        *,
        kind: str | None = None,
        limit: int | None = None,
        min_score: float = MIN_SCORE,
    ) -> tuple[MemoryMatch, ...]:
        tokens = _tokens(query)
        if not tokens:
            return ()
        requested = self.max_recall if limit is None else max(1, min(int(limit), self.max_recall))
        entries = self.store.learn(project, kind=kind)
        matches: list[MemoryMatch] = []
        for entry in entries:
            data = entry.get("data", {}) if isinstance(entry, Mapping) else {}
            searchable = " ".join(
                str(data.get(field, "")) for field in ("task", "summary", "subject", "value", "source")
            )
            score = _similarity(tokens, _tokens(searchable))
            if score >= max(0.0, float(min_score)):
                matches.append(
                    MemoryMatch(
                        round(score, 6),
                        str(entry.get("project", project)),
                        str(entry.get("kind", "")),
                        str(entry.get("outcome", "")),
                        str(entry.get("fingerprint", "")),
                        data,
                    )
                )
        matches.sort(key=lambda item: (-item.score, item.kind, item.fingerprint))
        return tuple(matches[:requested])

    def history(self, project: str, *, kind: str | None = None) -> tuple[Mapping[str, Any], ...]:
        return self.store.learn(project, kind=kind)


__all__ = ["MAX_RECALL", "MemoryMatch", "PersistentMemory"]
