from __future__ import annotations
import json, os, re
from dataclasses import dataclass
from typing import Callable, Mapping
from .ai_coding_brain import RepositoryContext, _redact
from .self_improvement import PatchCandidate

@dataclass(frozen=True)
class ChatProviderConfig:
    endpoint: str
    model: str
    api_key_env: str
    timeout_seconds: float = 60.0

class OpenAICompatibleCodingModel:
    """Provider-neutral coding model for OpenAI-compatible chat endpoints."""
    def __init__(self, config: ChatProviderConfig, *, http_post: Callable | None = None):
        self.config, self._http_post = config, http_post
    def _post(self, payload: Mapping[str, object], headers: Mapping[str, str]):
        if self._http_post is not None:
            return self._http_post(self.config.endpoint, headers, payload, self.config.timeout_seconds)
        import httpx
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.post(self.config.endpoint, headers=dict(headers), json=dict(payload))
            response.raise_for_status()
            return response.json()
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
            "output_schema": {"unified_diff":"string", "file_contents":{"path":"complete UTF-8 file"}, "summary":"string", "test_commands":["proposal commands only"]},
            "constraints": ["Return JSON only.", "Never include secrets or private keys.", "Do not touch .git, .env, .github/workflows, or state/secrets.", "Do not merge, deploy, bill, or make external side effects."],
        }
        payload = {"model": self.config.model, "temperature": 0, "messages":[
            {"role":"system","content":"You are a constrained software engineer. Produce only a reviewable patch candidate."},
            {"role":"user","content":json.dumps(prompt, ensure_ascii=True)},
        ]}
        result = self._post(payload, {"Authorization":f"Bearer {api_key}","Content-Type":"application/json"})
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str): return None
        match = re.search(r"\{.*\}", content, re.S)
        if not match: return None
        try:
            data = json.loads(match.group(0))
            file_contents = {str(k): str(v) for k, v in dict(data["file_contents"]).items()}
            return PatchCandidate(str(data["unified_diff"]), file_contents, str(data["summary"]), tuple(str(x) for x in data.get("test_commands", ())))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return PatchCandidate(str(data["unified_diff"]), {str(k):str(v) for k,v in dict(data["file_contents"]).items()}, str(data["summary"]), tuple(str(x) for x in data.get("test_commands",())))
