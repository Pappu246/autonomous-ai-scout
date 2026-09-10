from __future__ import annotations

import json

from .openrouter_free import chat_free


def plan_with_openrouter(task: str) -> tuple[str, tuple[str, ...]] | None:
    """Return a bounded read-only/test plan from OpenRouter's free router only."""
    if not task.strip():
        return None
    result = chat_free(
        "Return JSON only with keys summary and steps. Give at most 8 concrete read-only or test actions. "
        "Do not propose source writes, merges, deployments, credential changes, billing changes, or access-control bypasses.\n"
        + task.strip()
    )
    if not result.success:
        return None
    try:
        data = json.loads(result.text.strip())
        summary, steps = data.get("summary", ""), data.get("steps", [])
        if not isinstance(summary, str) or not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
            return None
        return summary, tuple(steps[:8])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
