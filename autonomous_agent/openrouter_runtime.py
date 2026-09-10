from __future__ import annotations

from .openrouter_free import chat_free


def plan_with_openrouter(task: str) -> tuple[str, tuple[str, ...]] | None:
    """Return a bounded read-only/test plan from OpenRouter's free router only."""
    if not task.strip():
        return None
    prompt = (
        "Return JSON only with keys summary and steps. "
        "Give at most 8 concrete read-only or test actions. Do not propose source writes, merges, deployments, "
        "credential changes, billing changes, or access-control bypasses.\nTask: " + task.strip()
    )
    result = chat_free(prompt)
    if not result.success:
        return None
    import json
    try:
        data = json.loads(result.text.strip())
        summary = data.get("summary", "")
        steps = data.get("steps", [])
        if not isinstance(summary, str) or not isinstance(steps, list):
            return None
        if not all(isinstance(step, str) for step in steps):
            return None
        return summary, tuple(steps[:8])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
