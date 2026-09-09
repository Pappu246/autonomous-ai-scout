from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateStore:
    """Small JSON state store for GitHub Actions persistence.

    The workflow can cache or commit this file later. Secrets are never stored here.
    """

    def __init__(self, path: str | Path = "state/scout_state.json") -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)
