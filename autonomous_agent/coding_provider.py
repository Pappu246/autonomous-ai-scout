from __future__ import annotations
import json, os, re
from dataclasses import dataclass
from typing import Callable, Mapping
from .ai_coding_brain import RepositoryContext, _redact
from .self_improvement import PatchCandidate


_SECRET_JSON = re.compile(
    r'(?i)(["\'](?:api[_-]?key|access[_-]?token|token|password|secret|authorization|credential)["\']\s*:\s*["\'])[^"\']+(["\'])'
)


class ProviderRequestError(RuntimeError):
    """Safe provider request failure with bounded diagnostic detail."""

    def __init__(self, status_code: int, detail: str = ""):
        self.status_code = int(status_code)
        super().__init__(
            f"provider returned HTTP {self.status_code}"
            + (f": {detail}" if detail else "")
        )

@dataclass(frozen=True)
class ChatProviderConfig:
    endpoint: str
    model: str
    api_key_env: str
    timeout_seconds: float = 60.0
    temperature: float | None = None
    structured_output: bool = False


_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "unified_diff": {"type": "string"},
        "file_contents": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "summary": {"type": "string"},
        "test_commands": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["unified_diff", "file_contents", "summary", "test_commands"],
    "additionalProperties": False,
}

class OpenAICompatibleCodingModel:
    """Provider-neutral coding model for OpenAI-compatible chat endpoints."""
    def __init__(self, config: ChatProviderConfig, *, http_post: Callable | None = None):
        self.config, self._http_post = config, http_post
    def _post(self, payload: Mapping[str, object], headers: Mapping[str, str]):
        import httpx

        try:
            if self._http_post is not None:
                return self._http_post(
                    self.config.endpoint,
                    headers,
                    payload,
                    self.config.timeout_seconds,
                )
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.post(
                    self.config.endpoint,
                    headers=dict(headers),
                    json=dict(payload),
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            body = _SECRET_JSON.sub(r"\1[REDACTED]\2", _redact(exc.response.text))[:500]
            raise ProviderRequestError(exc.response.status_code, body) from exc
    def generate_patch(self, *, proposal, context: RepositoryContext, feedback="", previous=None):
        api_key = os.getenv(self.config.api_key_env)
        if not api_key: return None
        prompt = {
            "task": proposal.problem, "solution": proposal.proposed_solution,
            "validation": proposal.validation_strategy, "affected_area": proposal.affected_area,
            "repository": context.repository,
            "files": [{"path": f.path, "content": f.content} for f in context.files],
            "feedback": _redact(feedback)[:4000],
            "previous_summary": previous.summary if previous else "",
            "output_schema": {"unified_diff":"string", "file_contents":{"path":"complete UTF-8 file"}, "summary":"string", "test_commands":["executable commands only; omit when validation plan is prose-only"]},
            "validation_rule": "Prefer python -m pytest for Python tests; never convert prose validation steps into shell commands or invent an executable command.",
            "constraints": ["Return JSON only.", "Never include secrets or private keys.", "Do not touch .git, .env, .github/workflows, or state/secrets.", "Do not merge, deploy, bill, or make external side effects."],
        }
        payload = {"model": self.config.model, "messages":[
            {"role":"system","content":"You are a constrained software engineer. Produce only a reviewable patch candidate."},
            {"role":"user","content":json.dumps(prompt, ensure_ascii=True)},
        ]}
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature
        if self.config.structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "patch_candidate",
                    "schema": _PATCH_SCHEMA,
                },
            }
        result = self._post(payload, {"Authorization":f"Bearer {api_key}","Content-Type":"application/json"})
        try:
            message = result["choices"][0]["message"]
            if not isinstance(message, Mapping):
                return None
            if isinstance(message.get("parsed"), Mapping):
                data = dict(message["parsed"])
                text = ""
            else:
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(
                        str(item.get("text", ""))
                        for item in content
                        if isinstance(item, Mapping)
                    )
                if not isinstance(content, str):
                    return None
                text = content.strip()
            if text.startswith("```") and text.endswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text, count=1)
                text = re.sub(r"\s*```$", "", text, count=1).strip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", text, re.S)
                if not match: return None
                data = json.loads(match.group(0))
            if not isinstance(data, dict): return None
            raw_files = data.get("file_contents", {})
            test_commands = data.get("test_commands", ())
            if not isinstance(raw_files, dict) or not isinstance(test_commands, (list, tuple)): return None
            return PatchCandidate(
                str(data["unified_diff"]),
                {str(k): str(v) for k, v in raw_files.items()},
                str(data["summary"]),
                tuple(str(x) for x in test_commands),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
