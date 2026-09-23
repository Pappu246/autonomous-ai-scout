from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock


class TriggerError(ValueError):
    pass


@dataclass(frozen=True)
class Trigger:
    trigger_id: str
    event: str
    fingerprint: str
    cooldown_seconds: int
    enabled: bool = True


class TriggerRegistry:
    def __init__(self, *, max_triggers: int = 256, path: str | Path | None = None):
        if max_triggers < 1 or max_triggers > 2048:
            raise ValueError("max_triggers is out of bounds")
        self.max_triggers = max_triggers
        self.path = Path(path) if path is not None else None
        self._triggers: dict[str, Trigger] = {}
        self._last_fired: dict[str, float] = {}
        self._lock = Lock()
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TriggerError("trigger store is unreadable") from exc
        if not isinstance(payload, dict):
            raise TriggerError("trigger store schema is invalid")
        raw_triggers = payload.get("triggers", [])
        raw_fired = payload.get("last_fired", {})
        if not isinstance(raw_triggers, list) or not isinstance(raw_fired, dict):
            raise TriggerError("trigger store schema is invalid")
        for item in raw_triggers[: self.max_triggers]:
            if not isinstance(item, dict):
                raise TriggerError("trigger record is invalid")
            trigger = Trigger(
                str(item["trigger_id"]),
                str(item["event"]),
                str(item["fingerprint"]),
                int(item["cooldown_seconds"]),
                bool(item.get("enabled", True)),
            )
            if trigger.trigger_id in self._triggers:
                raise TriggerError("duplicate trigger id")
            self._triggers[trigger.trigger_id] = trigger
        self._last_fired = {str(key): float(value) for key, value in list(raw_fired.items())[: self.max_triggers]}

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "triggers": [asdict(item) for item in self._triggers.values()],
            "last_fired": self._last_fired,
        }
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def register(self, event: str, payload: object, *, cooldown_seconds: int = 60) -> Trigger:
        event = " ".join(str(event).split())
        if not event or len(event) > 256:
            raise TriggerError("trigger event is invalid")
        if cooldown_seconds < 0 or cooldown_seconds > 86400:
            raise TriggerError("trigger cooldown is invalid")
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        trigger_id = hashlib.sha256((event + "\0" + fingerprint).encode()).hexdigest()[:32]
        with self._lock:
            if trigger_id not in self._triggers and len(self._triggers) >= self.max_triggers:
                raise TriggerError("trigger registry is full")
            trigger = Trigger(trigger_id, event, fingerprint, cooldown_seconds)
            self._triggers[trigger_id] = trigger
            self._save()
            return trigger

    def fire(self, trigger_id: str) -> bool:
        with self._lock:
            trigger = self._triggers.get(str(trigger_id))
            if trigger is None or not trigger.enabled:
                return False
            now = time.time()
            last = self._last_fired.get(trigger.trigger_id, 0.0)
            if now - last < trigger.cooldown_seconds:
                return False
            self._last_fired[trigger.trigger_id] = now
            self._save()
            return True

    def disable(self, trigger_id: str) -> None:
        with self._lock:
            trigger = self._triggers.get(str(trigger_id))
            if trigger is not None:
                self._triggers[trigger.trigger_id] = Trigger(
                    trigger.trigger_id,
                    trigger.event,
                    trigger.fingerprint,
                    trigger.cooldown_seconds,
                    False,
                )
                self._save()


__all__ = ["Trigger", "TriggerError", "TriggerRegistry"]
