from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx

from .models import AccessStatus, ModelCandidate
from .openrouter_free import chat_free
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


def _parse_plan(task: str, model: str, provider: str, text: str) -> LLMPlan | None:
    try:
        parsed = _extract_json(text)
        steps = parsed.get("steps", [])
        summary = parsed.get("summary", "")
        if not isinstance(summary, str) or not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
            return None
        steps = steps[:12]
        return LLMPlan(
            task=task,
            model=model,
            provider=provider,
            summary=summary,
            steps=tuple(steps),
            requires_approval=_approval_required(task, steps),
            raw=text,
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _planner_prompt(task: str) -> str:
    return (
        "You are a bounded software task planner. Return JSON only with keys: summary, steps, requires_approval. "
        "steps must be a short array of concrete read-only or test actions. "
        "Set requires_approval=true for any source modification, merge, deployment, credential change, or destructive action. "
        f"Task: {task}"
    )


def _plan_with_gemini(task: str, model: str) -> LLMPlan | None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        response = httpx.post(url, params={"key": key}, json={"contents": [{"parts": [{"text": _planner_prompt(task)}]}]}, timeout=20)
        if response.status_code in {401, 403, 429}:
            return None
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_plan(task, model, "gemini", text)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _plan_with_openrouter(task: str) -> LLMPlan | None:
    result = chat_free(_planner_prompt(task))
    if not result.success:
        return None
    return _parse_plan(task, "openrouter/free", "openrouter", result.text)


def plan_with_free_llm(task: str, candidates: list[ModelCandidate]) -> LLMPlan | None:
    """Plan with a verified-free selected model; fallback is only the explicit OpenRouter free router."""
    verified = [c for c in candidates if c.access_status is AccessStatus.VERIFIED_FREE]
    decision = choose_model(verified, task)
    if decision.model is None:
        return _plan_with_openrouter(task)
    if decision.model.provider == "gemini":
        plan = _plan_with_gemini(task, decision.model.model)
        if plan is not None:
            return plan
        return _plan_with_openrouter(task)
    if decision.model.provider == "openrouter":
        return _plan_with_openrouter(task)
    return _plan_with_openrouter(task)
