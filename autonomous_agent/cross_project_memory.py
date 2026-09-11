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
MAX_LIST_ITEMS = 50
_SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|access[_-]?token|auth(?:orization)?|token|secret|password|credential|private[_-]?key)")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|auth(?:orization)?|token|secret|password|credential)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----"),
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


def _safe_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _safe_data(value)
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:MAX_LIST_ITEMS]]
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _safe_text(value)


def _safe_data(data: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in data.items():
        normalized_key = _safe_text(key)
        if _SECRET_KEY_RE.search(normalized_key):
            safe[normalized_key] = "[REDACTED]"
        else:
            safe[normalized_key] = _safe_value(value)
    return safe


def _entry_hash(entry: Mapping[str, Any]) -> str:
    payload = {
        "project": entry.get("project"),
        "kind": entry.get("kind"),
        "fingerprint": entry.get("fingerprint"),
        "outcome": entry.get("outcome"),
        "data": entry.get("data"),
        "previous_hash": entry.get("previous_hash", ""),
    }
    return _digest(payload)


class CrossProjectMemory:
    """Bounded, tamper-evident memory that stores evidence, not authority or secrets."""

    def __init__(self, path: Path, *, max_entries: int = MAX_ENTRIES, max_entries_per_project: int = MAX_ENTRIES_PER_PROJECT):
        self.path = Path(path)
        self.max_entries = max(1, min(int(max_entries), MAX_ENTRIES))
        self.max_entries_per_project = max(1, min(int(max_entries_per_project), MAX_ENTRIES_PER_PROJECT))

    def _load(self) -> tuple[list[dict[str, Any]], bool]:
        if not self.path.exists():
            return [], True
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return [], False
        if not isinstance(value, list):
            return [], False
        entries = [item for item in value if isinstance(item, dict)]
        if len(entries) != len(value):
            return [], False
        previous = ""
        for item in entries:
            if item.get("previous_hash", "") != previous:
                return [], False
            if item.get("event_hash") != _entry_hash(item):
                return [], False
            previous = str(item["event_hash"])
        return entries, True

    def _save(self, entries: list[dict[str, Any]]) -> bool:
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
        sealed: list[dict[str, Any]] = []
        previous = ""
        for item in kept:
            clean = {
                "project": item.get("project"),
                "kind": item.get("kind"),
                "fingerprint": item.get("fingerprint"),
                "outcome": item.get("outcome"),
                "data": item.get("data", {}),
                "previous_hash": previous,
            }
            clean["event_hash"] = _entry_hash(clean)
            sealed.append(clean)
            previous = clean["event_hash"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(sealed, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    def record(self, event: MemoryEvent) -> bool:
        entries, valid = self._load()
        if not valid:
            return False
        safe = {
            "project": _safe_text(event.project),
            "kind": _safe_text(event.kind),
            "fingerprint": _digest(event.project, event.kind, event.fingerprint),
            "outcome": _safe_text(event.outcome),
            "data": _safe_data(event.data),
        }
        entries.append(safe)
        return self._save(entries)

    def has(self, *, project: str, kind: str, fingerprint: str) -> bool:
        entries, valid = self._load()
        if not valid:
            return False
        project_name, kind_name = _safe_text(project), _safe_text(kind)
        stored = _digest(project, kind, fingerprint)
        return any(
            item.get("project") == project_name
            and item.get("kind") == kind_name
            and item.get("fingerprint") in {stored, _safe_text(fingerprint)}
            for item in entries
        )

    def record_task(self, project: str, task: str, *, intent: str, outcome: str) -> bool:
        return self.record(MemoryEvent(project, "task", _digest(project, task), outcome, {"task_digest": _digest(task), "intent": intent}))

    def record_tool_execution(self, project: str, tool: str, *, execution_id: str, outcome: str, attempts: int) -> bool:
        return self.record(MemoryEvent(project, "tool_execution", _digest(project, tool, execution_id), outcome, {"tool": tool, "execution_id": execution_id, "attempts": max(0, int(attempts))}))

    def record_benchmark(self, project: str, benchmark: str, *, score: float, provider: str) -> bool:
        return self.record(MemoryEvent(project, "benchmark", _digest(project, benchmark, provider, score), "completed", {"benchmark": benchmark, "provider": provider, "score": score}))

    def record_provider_availability(self, provider: str, *, available: bool, reason: str = "") -> bool:
        return self.record(MemoryEvent("_global", "provider_availability", _digest(provider, available, reason), "available" if available else "unavailable", {"provider": provider, "reason": reason}))

    def record_finding(self, project: str, finding: str, *, severity: str, status: str = "open") -> bool:
        fingerprint = _digest(project, finding, severity)
        if self.has(project=project, kind="finding", fingerprint=fingerprint):
            return False
        return self.record(MemoryEvent(project, "finding", fingerprint, status, {"finding_digest": _digest(finding), "severity": severity}))

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
        entries, valid = self._load()
        if not valid:
            return True
        project_name, subject_name = _safe_text(project), _safe_text(subject)
        target = _digest(project, "change", fingerprint)
        previous = [item for item in entries if item.get("project") == project_name and item.get("kind") == "change" and item.get("data", {}).get("subject") == subject_name]
        return not previous or previous[-1].get("fingerprint") != target

    def record_change(self, project: str, subject: str, fingerprint: str) -> bool:
        return self.record(MemoryEvent(project, "change", fingerprint, "detected", {"subject": subject}))

    def learn(self, project: str, *, kind: str | None = None) -> tuple[Mapping[str, Any], ...]:
        entries, valid = self._load()
        if not valid:
            return ()
        project_name = _safe_text(project)
        kind_name = _safe_text(kind) if kind is not None else None
        return tuple(item for item in entries if item.get("project") == project_name and (kind_name is None or item.get("kind") == kind_name))
