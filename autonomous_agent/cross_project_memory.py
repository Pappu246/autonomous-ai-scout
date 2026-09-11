from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


MAX_ENTRIES = 1000
MAX_ENTRIES_PER_PROJECT = 200
MAX_STRING_LENGTH = 512
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|credential)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|github_pat|xoxb|xoxp)-[A-Za-z0-9_\-]+"),
    re.compile(r"\bAIza[0-9A-Za-z_-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
)


@dataclass(frozen=True)
class MemoryEvent:
    project: str
    kind: str
    fingerprint: str
    outcome: str
    data: Mapping[str, Any]


def _digest(*parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_text(value: object) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text[:MAX_STRING_LENGTH]


def _safe_data(data: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in data.items():
        normalized_key = _safe_text(key)
        if isinstance(value, Mapping):
            safe[normalized_key] = _safe_data(value)
        elif isinstance(value, (list, tuple)):
            safe[normalized_key] = [_safe_text(item) for item in value[:50]]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[normalized_key] = _safe_text(value) if isinstance(value, str) else value
        else:
            safe[normalized_key] = _safe_text(value)
    return safe


class CrossProjectMemory:
    """Bounded, append-oriented project memory; it stores evidence, not authority."""

    def __init__(self, path: Path, *, max_entries: int = MAX_ENTRIES, max_entries_per_project: int = MAX_ENTRIES_PER_PROJECT):
        self.path = Path(path)
        self.max_entries = max(1, min(int(max_entries), MAX_ENTRIES))
        self.max_entries_per_project = max(1, min(int(max_entries_per_project), MAX_ENTRIES_PER_PROJECT))

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _save(self, entries: list[dict[str, Any]]) -> None:
        bounded = entries[-self.max_entries :]
        counts: dict[str, int] = {}
        kept: list[dict[str, Any]] = []
        for item in reversed(bounded):
            project = str(item.get("project", "_global"))
            count = counts.get(project, 0)
            if count >= self.max_entries_per_project:
                continue
            counts[project] = count + 1
            kept.append(item)
        kept.reverse()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(kept, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)

    def record(self, event: MemoryEvent) -> bool:
        project = _safe_text(event.project)
        kind = _safe_text(event.kind)
        outcome = _safe_text(event.outcome)
        safe = {
            "project": project,
            "kind": kind,
            "fingerprint": _safe_text(event.fingerprint),
            "outcome": outcome,
            "data": _safe_data(event.data),
        }
        entries = self._load()
        entries.append(safe)
        self._save(entries)
        return True

    def has(self, *, project: str, kind: str, fingerprint: str) -> bool:
        target = (_safe_text(project), _safe_text(kind), _safe_text(fingerprint))
        return any((item.get("project"), item.get("kind"), item.get("fingerprint")) == target for item in self._load())

    def record_task(self, project: str, task: str, *, intent: str, outcome: str) -> bool:
        return self.record(MemoryEvent(project, "task", _digest(project, task), outcome, {"task_digest": _digest(task), "intent": intent}))

    def record_tool_execution(self, project: str, tool: str, *, execution_id: str, outcome: str, attempts: int) -> bool:
        return self.record(MemoryEvent(project, "tool_execution", _digest(project, tool, execution_id), outcome, {"tool": tool, "execution_id": execution_id, "attempts": max(0, int(attempts))}))

    def record_benchmark(self, project: str, benchmark: str, *, score: float, provider: str) -> bool:
        return self.record(MemoryEvent(project, "benchmark", _digest(project, benchmark, provider, score), "completed", {"benchmark": benchmark, "provider": provider, "score": score}))

    def record_provider_availability(self, provider: str, *, available: bool, reason: str = "") -> bool:
        return self.record(MemoryEvent("_global", "provider_availability", _digest(provider, available, reason), "available" if available else "unavailable", {"provider": provider, "reason": reason}))

    def record_finding(self, project: str, finding: str, *, severity: str, status: str = "open") -> bool:
        return self.record(MemoryEvent(project, "finding", _digest(project, finding, severity), status, {"finding_digest": _digest(finding), "severity": severity}))

    def record_recommendation(self, project: str, recommendation: str, *, status: str) -> bool:
        fingerprint = _digest(project, recommendation)
        return self.record(MemoryEvent(project, "recommendation", fingerprint, status, {"recommendation_digest": _digest(recommendation)}))

    def recommendation_needed(self, project: str, recommendation: str) -> bool:
        return not self.has(project=project, kind="recommendation", fingerprint=_digest(project, recommendation))

    def record_improvement(self, project: str, improvement: str, *, status: str) -> bool:
        return self.record(MemoryEvent(project, "improvement", _digest(project, improvement), status, {"improvement_digest": _digest(improvement)}))

    def record_health_baseline(self, project: str, baseline: Mapping[str, Any]) -> bool:
        safe = _safe_data(baseline)
        return self.record(MemoryEvent(project, "health_baseline", _digest(project, safe), "observed", {"baseline": safe}))

    def change_detected(self, project: str, subject: str, fingerprint: str) -> bool:
        previous = [item for item in self._load() if item.get("project") == _safe_text(project) and item.get("kind") == "change" and item.get("data", {}).get("subject") == _safe_text(subject)]
        return not previous or previous[-1].get("fingerprint") != _safe_text(fingerprint)

    def record_change(self, project: str, subject: str, fingerprint: str) -> bool:
        return self.record(MemoryEvent(project, "change", fingerprint, "detected", {"subject": subject}))

    def learn(self, project: str, *, kind: str | None = None) -> tuple[Mapping[str, Any], ...]:
        entries = self._load()
        project_name = _safe_text(project)
        selected = [item for item in entries if item.get("project") == project_name and (kind is None or item.get("kind") == _safe_text(kind))]
        return tuple(selected)
