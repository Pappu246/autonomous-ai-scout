from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
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
    def __init__(self, *, max_triggers: int = 256):
        if max_triggers < 1 or max_triggers > 2048:
            raise ValueError("max_triggers is out of bounds")
        self.max_triggers = max_triggers
        self._triggers: dict[str, Trigger] = {}
        self._last_fired: dict[str, float] = {}
        self._lock = Lock()

    def register(self, event: str, payload: object, *, cooldown_seconds: int = 60) -> Trigger:
        event = " ".join(str(event).split())
        if not event or len(event) > 256:
            raise TriggerError("trigger event is invalid")
        if cooldown_seconds < 0 or cooldown_seconds > 86400:
            raise TriggerError("trigger cooldown is invalid")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        trigger_id = hashlib.sha256((event + "\0" + fingerprint).encode()).hexdigest()[:32]
        with self._lock:
            if trigger_id not in self._triggers and len(self._triggers) >= self.max_triggers:
                raise TriggerError("trigger registry is full")
            trigger = Trigger(trigger_id, event, fingerprint, cooldown_seconds)
            self._triggers[trigger_id] = trigger
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


__all__ = ["Trigger", "TriggerError", "TriggerRegistry"]
