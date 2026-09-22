from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .persistent_memory import MemoryMatch, PersistentMemory

MAX_CONTEXT_CHARS = 24_000
MAX_CONTEXT_ITEMS = 32
MAX_ITEM_CHARS = 4_000
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ContextItem:
    kind: str
    content: str
    priority: int
    source: str
    fingerprint: str


@dataclass(frozen=True)
class ContextPacket:
    task: str
    items: tuple[ContextItem, ...]
    text: str
    digest: str
    dropped_items: int


def _clean(value: object) -> str:
    return _SPACE.sub(" ", str(value)).strip()[:MAX_ITEM_CHARS]


def _fingerprint(item: ContextItem) -> str:
    payload = {
        "content": item.content,
        "kind": item.kind,
        "priority": item.priority,
        "source": item.source,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


class ContextManager:
    """Build a bounded task context from live inputs and persistent memory."""

    def __init__(
        self,
        *,
        max_chars: int = MAX_CONTEXT_CHARS,
        max_items: int = MAX_CONTEXT_ITEMS,
    ) -> None:
        self.max_chars = max(1_000, min(int(max_chars), MAX_CONTEXT_CHARS))
        self.max_items = max(1, min(int(max_items), MAX_CONTEXT_ITEMS))

    def build(
        self,
        task: str,
        *,
        plan: str = "",
        observations: Iterable[str] = (),
        memory: PersistentMemory | None = None,
        project: str | None = None,
        memory_query: str | None = None,
        pinned: Iterable[str] = (),
    ) -> ContextPacket:
        normalized_task = _clean(task)
        raw: list[ContextItem] = [
            ContextItem("task", normalized_task, 1000, "live", ""),
        ]
        if plan.strip():
            raw.append(ContextItem("plan", _clean(plan), 900, "live", ""))
        for observation in observations:
            clean = _clean(observation)
            if clean:
                raw.append(ContextItem("observation", clean, 700, "live", ""))
        for value in pinned:
            clean = _clean(value)
            if clean:
                raw.append(ContextItem("pinned", clean, 950, "live", ""))
        if memory is not None and project and memory_query:
            for match in memory.recall(project, memory_query, limit=8):
                summary = self._memory_summary(match)
                if summary:
                    raw.append(ContextItem("memory", summary, int(100 + match.score * 500), match.kind, match.fingerprint))

        dedup: dict[tuple[str, str], ContextItem] = {}
        for item in raw:
            normalized = ContextItem(item.kind, _clean(item.content), item.priority, item.source, item.fingerprint)
            dedup.setdefault((normalized.kind, normalized.content), normalized)
        ordered = sorted(dedup.values(), key=lambda item: (-item.priority, item.kind, item.fingerprint))

        selected: list[ContextItem] = []
        chars = 0
        for item in ordered:
            if len(selected) >= self.max_items:
                break
            line = f"[{item.kind}] {item.content}"
            projected = chars + len(line) + 1
            if projected > self.max_chars:
                continue
            if not item.fingerprint:
                item = ContextItem(item.kind, item.content, item.priority, item.source, _fingerprint(item))
            selected.append(item)
            chars = projected
        text = "\n".join(f"[{item.kind}] {item.content}" for item in selected)
        digest = hashlib.sha256(
            json.dumps(
                {"task": normalized_task, "items": [item.fingerprint for item in selected]},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return ContextPacket(normalized_task, tuple(selected), text, digest, max(0, len(ordered) - len(selected)))

    @staticmethod
    def _memory_summary(match: MemoryMatch) -> str:
        data = match.data
        summary = data.get("summary") or data.get("value") or data.get("subject") or data.get("task")
        if not summary:
            return ""
        return f"{summary} (outcome={match.outcome}, score={match.score:.3f})"


__all__ = ["ContextItem", "ContextManager", "ContextPacket", "MAX_CONTEXT_CHARS", "MAX_CONTEXT_ITEMS"]
