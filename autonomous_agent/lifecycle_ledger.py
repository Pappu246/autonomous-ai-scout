from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .action_lifecycle import LifecycleState, can_transition


@dataclass(frozen=True)
class LifecycleEvent:
    action_id: str
    sequence: int
    from_state: str
    to_state: str
    timestamp: str
    previous_hash: str
    event_hash: str


def _canonical_payload(event: dict[str, str]) -> str:
    return json.dumps(
        {key: value for key, value in event.items() if key != "event_hash"},
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_events(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, str]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            return []
        if not isinstance(item, dict):
            return []
        events.append({str(key): str(value) for key, value in item.items()})
    return events


def append_transition(path: Path, action_id: str, current: LifecycleState | str, target: LifecycleState | str) -> LifecycleEvent:
    """Append one valid lifecycle transition to an integrity-chained ledger."""
    action_id = action_id.strip() if isinstance(action_id, str) else ""
    if not action_id:
        raise ValueError("action_id must not be empty")
    decision = can_transition(current, target)
    if not decision.allowed:
        raise ValueError(decision.reason)

    current_state = LifecycleState(current).value
    target_state = LifecycleState(target).value
    existing = _read_events(path)
    if path.exists() and not existing and path.read_text(encoding="utf-8").strip():
        raise ValueError("lifecycle ledger is unreadable")

    action_events = [event for event in existing if event.get("action_id") == action_id]
    expected_from = action_events[-1]["to_state"] if action_events else LifecycleState.PROPOSED.value
    if action_events and expected_from != current_state:
        raise ValueError("ledger current state does not match requested transition")
    if not action_events and current_state != LifecycleState.PROPOSED.value:
        raise ValueError("first ledger transition must begin at proposed state")

    sequence = len(action_events) + 1
    previous_hash = action_events[-1]["event_hash"] if action_events else ""
    timestamp = datetime.now(timezone.utc).isoformat()
    event: dict[str, str] = {
        "action_id": action_id,
        "sequence": str(sequence),
        "from_state": current_state,
        "to_state": target_state,
        "timestamp": timestamp,
        "previous_hash": previous_hash,
    }
    event_hash = hashlib.sha256(_canonical_payload(event).encode("utf-8")).hexdigest()
    event["event_hash"] = event_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return LifecycleEvent(action_id, sequence, current_state, target_state, timestamp, previous_hash, event_hash)


def verify_ledger(path: Path) -> bool:
    """Verify hash links, sequence monotonicity, and valid state transitions for every action."""
    if not path.exists():
        return True
    events = _read_events(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if raw.strip() and not events:
        return False

    previous_by_action: dict[str, str] = {}
    sequence_by_action: dict[str, int] = {}
    state_by_action: dict[str, str] = {}
    for event in events:
        required = {"action_id", "sequence", "from_state", "to_state", "timestamp", "previous_hash", "event_hash"}
        if not required <= event.keys() or not event["action_id"].strip():
            return False
        try:
            sequence = int(event["sequence"])
            LifecycleState(event["from_state"])
            LifecycleState(event["to_state"])
        except (TypeError, ValueError):
            return False
        action_id = event["action_id"]
        expected_sequence = sequence_by_action.get(action_id, 0) + 1
        if sequence != expected_sequence:
            return False
        expected_previous = previous_by_action.get(action_id, "")
        if event["previous_hash"] != expected_previous:
            return False
        if action_id not in state_by_action and event["from_state"] != LifecycleState.PROPOSED.value:
            return False
        if action_id in state_by_action and event["from_state"] != state_by_action[action_id]:
            return False
        decision = can_transition(event["from_state"], event["to_state"])
        if not decision.allowed:
            return False
        expected_hash = hashlib.sha256(_canonical_payload({key: value for key, value in event.items()}).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(event["event_hash"], expected_hash):
            return False
        previous_by_action[action_id] = event["event_hash"]
        sequence_by_action[action_id] = sequence
        state_by_action[action_id] = event["to_state"]
    return True


def load_action_events(path: Path, action_id: str) -> tuple[LifecycleEvent, ...]:
    """Return one action's ordered ledger events; invalid ledgers return no events."""
    if not verify_ledger(path):
        return ()
    result: list[LifecycleEvent] = []
    for event in _read_events(path):
        if event.get("action_id") != action_id:
            continue
        result.append(LifecycleEvent(
            event["action_id"],
            int(event["sequence"]),
            event["from_state"],
            event["to_state"],
            event["timestamp"],
            event["previous_hash"],
            event["event_hash"],
        ))
    return tuple(result)


def action_state(path: Path, action_id: str) -> LifecycleState | None:
    """Return the last trusted state for an action, or None for missing/invalid history."""
    if not isinstance(action_id, str) or not action_id.strip() or not verify_ledger(path):
        return None
    events = load_action_events(path, action_id)
    if not events:
        return None
    try:
        return LifecycleState(events[-1].to_state)
    except (TypeError, ValueError):
        return None
