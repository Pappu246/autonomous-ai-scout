from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx

from .models import AccessStatus, ModelCandidate
from .router import choose_model


@dataclass(frozen=True)
class LLMPlan:
    task: str
    model: str
    provider: str
    summary: str
    steps: tuple[str, ...]
    requires_approval: bool
    raw: str = ""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM did not return a JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM JSON root must be an object")
    return value


def _approval_required(task: str, steps: list[str]) -> bool:
    """Enforce approval from policy-sensitive language, independent of LLM output."""
    text = f"{task} {' '.join(steps)}".lower()
    sensitive_terms = (
        "write", "modify", "change", "edit", "create file", "delete", "remove",
        "fix", "implement", "refactor", "deploy", "deployment", "merge", "push",
        "credential", "secret", "token", "password", "production", "destructive",
    )
    return any(term in text for term in sensitive_terms)


def plan_with_free_llm(task: str, candidates: list[ModelCandidate]) -> LLMPlan | None:
    """Use only a currently verified-free Gemini candidate; never fall back to paid access."""
    decision = choose_model([c for c in candidates if c.access_status is AccessStatus.VERIFIED_FREE], task)
    if decision.model is None or decision.model.provider != "gemini":
        return None
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None

    model = decision.model.model
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    prompt = (
        "You are a bounded software task planner. Return JSON only with keys: summary, steps, requires_approval. "
        "steps must be a short array of concrete read-only or test actions. "
        "Set requires_approval=true for any source modification, merge, deployment, credential change, or destructive action. "
        f"Task: {task}"
    )
    try:
        response = httpx.post(url, params={"key": key}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
        if response.status_code in {401, 403, 429}:
            return None
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = _extract_json(text)
        steps = parsed.get("steps", [])
        summary = parsed.get("summary", "")
        if not isinstance(summary, str) or not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
            return None
        # Never trust the model's approval flag: derive it from the requested task and generated actions.
        requires_approval = _approval_required(task, steps)
        return LLMPlan(task=task, model=model, provider=decision.model.provider, summary=summary, steps=tuple(steps[:12]), requires_approval=requires_approval, raw=text)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None
