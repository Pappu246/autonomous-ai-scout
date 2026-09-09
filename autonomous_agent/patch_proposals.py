from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatchProposal:
    id: str
    task: str
    summary: str
    steps: tuple[str, ...]
    test_command: str
    status: str = "proposed"
    applied: bool = False


def build_patch_proposal(task: str, steps: tuple[str, ...], summary: str = "") -> PatchProposal:
    """Describe a possible code change without creating or applying source patches."""
    normalized = " ".join(task.split())
    bounded_steps = tuple(steps[:12])
    raw = json.dumps([normalized, bounded_steps], separators=(",", ":"), sort_keys=True)
    proposal_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return PatchProposal(
        id=proposal_id,
        task=normalized,
        summary=summary or "Sandboxed proposal only; source files are not modified.",
        steps=bounded_steps,
        test_command="python -m pytest -q",
    )


def save_proposal(path: Path, proposal: PatchProposal) -> None:
    """Persist proposal metadata only; never writes source files or executes commands."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(proposal), indent=2) + "\n", encoding="utf-8")
