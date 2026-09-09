from __future__ import annotations

import json
import os
from pathlib import Path

from .discovery import candidates_from_registry
from .models import ScoutReport
from .reporting import render_markdown
from .state import StateStore
from .verify import verify_candidate

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "providers.json"
REPORT_PATH = ROOT / "state" / "latest_report.md"


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def run() -> ScoutReport:
    registry = load_registry()
    candidates = candidates_from_registry(registry.get("providers", []))
    verified = [verify_candidate(candidate) for candidate in candidates]
    report = ScoutReport(models=verified)
    report.notes.append("Free-only policy is enabled; no paid provider is activated automatically.")
    return report


def main() -> int:
    report = run()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_markdown(report), encoding="utf-8")
    StateStore().save(
        {
            "last_run": report.generated_at.isoformat(),
            "verified_free_models": [f"{m.provider}/{m.model}" for m in report.models if m.access_status.value == "verified_free"],
            "report_path": str(REPORT_PATH),
            "github_repository": os.getenv("GITHUB_REPOSITORY", ""),
        }
    )
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
