from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable, Mapping


class EffectState(str, Enum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    FAILED = "failed"


@dataclass(frozen=True)
class EffectRecord:
    key: str
    state: EffectState
    result: Any = None
    error: str = ""
    created: bool = False


@dataclass(frozen=True)
class IdempotentResult:
    state: EffectState
    executed: bool
    result: Any = None
    error: str = ""


class EffectLedger:
    """Durable idempotency ledger for side-effect keys."""

    def __init__(self, path: str | Path = "state/effect_ledger.json") -> None:
        self.path = Path(path)
        self._lock = Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("effect ledger is unreadable") from exc
        if not isinstance(value, dict):
            raise ValueError("effect ledger has invalid schema")
        return value

    def _save(self, value: Mapping[str, Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def get(self, key: str) -> EffectRecord | None:
        with self._lock:
            item = self._load().get(key)
            if not isinstance(item, dict):
                return None
            try:
                state = EffectState(str(item["state"]))
            except (KeyError, ValueError) as exc:
                raise ValueError("effect ledger contains invalid state") from exc
            return EffectRecord(key, state, item.get("result"), str(item.get("error", "")), False)

    def prepare(self, key: str) -> EffectRecord:
        if not key.strip():
            raise ValueError("effect key is required")
        with self._lock:
            value = self._load()
            current = value.get(key)
            if current is not None:
                return EffectRecord(key, EffectState(str(current["state"])), current.get("result"), str(current.get("error", "")), False)
            value[key] = {"state": EffectState.PREPARED.value}
            self._save(value)
            return EffectRecord(key, EffectState.PREPARED, created=True)

    def commit(self, key: str, result: Any = None) -> EffectRecord:
        with self._lock:
            value = self._load()
            value[key] = {"state": EffectState.COMMITTED.value, "result": result}
            self._save(value)
            return EffectRecord(key, EffectState.COMMITTED, result)

    def fail(self, key: str, error: str) -> EffectRecord:
        with self._lock:
            value = self._load()
            value[key] = {"state": EffectState.FAILED.value, "error": error[:500]}
            self._save(value)
            return EffectRecord(key, EffectState.FAILED, error=error[:500])


Verifier = Callable[[Any], bool]


class IdempotentEffectRunner:
    """Execute one effect once per key and require recovery for ambiguous prepared state."""

    def __init__(self, ledger: EffectLedger) -> None:
        self.ledger = ledger

    def run(
        self,
        key: str,
        operation: Callable[[], Any],
        *,
        verify: Verifier | None = None,
    ) -> IdempotentResult:
        existing = self.ledger.get(key)
        if existing is not None:
            if existing.state is EffectState.COMMITTED:
                return IdempotentResult(EffectState.COMMITTED, False, existing.result)
            if existing.state is EffectState.PREPARED:
                return IdempotentResult(EffectState.PREPARED, False, error="effect is already prepared and requires recovery")
        record = self.ledger.prepare(key)
        if not record.created:
            return IdempotentResult(EffectState.PREPARED, False, error="effect is already prepared and requires recovery")
        try:
            result = operation()
            if verify is not None and not verify(result):
                self.ledger.fail(key, "post-effect verification failed")
                return IdempotentResult(EffectState.FAILED, True, error="post-effect verification failed")
            self.ledger.commit(key, result)
            return IdempotentResult(EffectState.COMMITTED, True, result)
        except Exception as exc:
            self.ledger.fail(key, f"effect failed: {type(exc).__name__}")
            return IdempotentResult(EffectState.FAILED, True, error=f"effect failed: {type(exc).__name__}")

    def recover_prepared(self, key: str, *, confirmed_result: Any = None) -> IdempotentResult:
        current = self.ledger.get(key)
        if current is None:
            return IdempotentResult(EffectState.FAILED, False, error="effect key is unknown")
        if current.state is EffectState.COMMITTED:
            return IdempotentResult(EffectState.COMMITTED, False, current.result)
        if current.state is not EffectState.PREPARED:
            return IdempotentResult(current.state, False, error=current.error)
        self.ledger.commit(key, confirmed_result)
        return IdempotentResult(EffectState.COMMITTED, False, confirmed_result)


__all__ = ["EffectLedger", "EffectRecord", "EffectState", "IdempotentEffectRunner", "IdempotentResult"]
