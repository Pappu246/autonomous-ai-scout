from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Opportunity

MAX_HISTORY = 30


def _key(opportunity: Opportunity) -> str:
    return opportunity.title.strip().lower()


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def update_history(path: Path, opportunities: list[Opportunity]) -> list[dict[str, Any]]:
    history = load_history(path)
    now = datetime.now(timezone.utc).isoformat()
    previous = history[-1] if history else {}
    previous_scores = {
        str(item.get("title", "")).strip().lower(): float(item.get("score", 0))
        for item in previous.get("opportunities", [])
        if item.get("title")
    }
    snapshot = {
        "timestamp": now,
        "opportunities": [
            {
                "title": item.title,
                "score": item.score,
                "score_delta": round(item.score - previous_scores.get(_key(item), item.score), 2),
            }
            for item in opportunities
        ],
    }
    updated = (history + [snapshot])[-MAX_HISTORY:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, indent=2, sort_keys=True), encoding="utf-8")
    return updated


def trend_notes(history: list[dict[str, Any]]) -> list[str]:
    if len(history) < 2:
        return ["Opportunity trend history starts on the first completed run; score deltas will appear on later runs."]
    current = history[-1].get("opportunities", [])
    notes: list[str] = []
    for item in current:
        title = str(item.get("title", ""))
        delta = float(item.get("score_delta", 0))
        if not title or delta == 0:
            continue
        direction = "up" if delta > 0 else "down"
        notes.append(f"Opportunity trend: {title} is {direction} {abs(delta):g} points vs. the previous run.")
    return notes
