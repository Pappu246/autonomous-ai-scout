from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field


_SECRET_RE = re.compile(r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+")


def _safe(value: object, limit: int = 2048) -> str:
    text = _SECRET_RE.sub("[REDACTED]", str(value))
    return text[:limit]


@dataclass
class TelemetryBuffer:
    max_events: int = 1000
    _events: list[dict[str, str]] = field(default_factory=list)

    def record(self, name: str, **fields: object) -> None:
        if len(self._events) >= self.max_events:
            return
        event = {"name": _safe(name, 256)}
        event.update({str(k): _safe(v) for k, v in fields.items()})
        self._events.append(event)

    def snapshot(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(item) for item in self._events)

    def fingerprint(self) -> str:
        raw = json.dumps(self._events, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


__all__ = ["TelemetryBuffer"]
