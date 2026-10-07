# AI release-gate trigger marker: guarded Gate 3/4/5 execution only.
# Final exact-SHA release verification marker.
# Final release-gate exact-SHA verification marker after handoff synchronization.
# Gate 3 exact-SHA verification marker; no runtime behavior change.
from __future__ import annotations
import difflib, json, os, re
from dataclasses import dataclass
from typing import Callable, Mapping
from .ai_coding_brain import RepositoryContext, _redact
from .prompt_injection_guard import PromptInjectionGuard, TrustLevel
from .self_improvement import PatchCandidate
from .patch_review import materialize_patch_file_contents


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

def _canonicalize_hunk_counts(text: str) -> str:
    """Repair lightweight-model hunk counts from their actual hunk lines."""
    lines = text.splitlines()
    out: list[str] = []
    hunk_start = None
    hunk_lines: list[str] = []

    def flush() -> None:
        nonlocal hunk_start, hunk_lines
        if hunk_start is None:
            return
        header = out[hunk_start]
        match = re.match(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$",
            header,
        )
        if match is None:
            hunk_start = None
            hunk_lines = []
            return
        old_start, _old_count, new_start, _new_count, tail = match.groups()
        old_count = sum(1 for line in hunk_lines if line and line[0] in {" ", "-"})
        new_count = sum(1 for line in hunk_lines if line and line[0] in {" ", "+"})
        out[hunk_start] = (
            f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{tail}"
        )
        hunk_start = None
        hunk_lines = []

    for line in lines:
        if line.startswith("@@ "):
            flush()
            hunk_start = len(out)
            hunk_lines = []
            out.append(line)
            continue
        if hunk_start is not None:
            if line.startswith(("diff --git ", "--- ", "+++ ")):
                flush()
            else:
                hunk_lines.append(line)
        out.append(line)
    flush()
    return "\n".join(out)

def _normalize_unified_diff(value: object) -> str:
    """Normalize harmless model formatting without changing patch semantics."""
    from textwrap import dedent

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    # Lightweight models may return the diff as a single JSON-escaped line.
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")

    # Accept a fenced diff wrapper, but keep the diff itself authoritative.
    lines = text.splitlines()
    fence = "`" * 3
    fence_start = next((i for i, line in enumerate(lines) if line.strip().startswith(fence)), None)
    if fence_start is not None:
        fence_end = next((i for i in range(fence_start + 1, len(lines)) if lines[i].strip() == fence), None)
        if fence_end is not None:
            lines = lines[fence_start + 1:fence_end]
            text = dedent("\n".join(lines)).strip()

    # Remove uniform presentation indentation while preserving the one-byte
    # context marker required by unified diff hunks.
    text = dedent(text).strip()

    # Canonicalize common lightweight-model unified headers only before the first
    # hunk. The strict patch reviewer remains authoritative for path safety.
    normalized: list[str] = []
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("@@ "):
            in_hunk = True
        if not in_hunk and line.lstrip().startswith("--- "):
            raw = line.lstrip()[4:].strip()
            if raw != "/dev/null" and not raw.startswith("a/"):
                raw = raw.removeprefix("./")
                line = "--- a/" + raw
        elif not in_hunk and line.lstrip().startswith("+++ "):
            raw = line.lstrip()[4:].strip()
            if raw != "/dev/null" and not raw.startswith("b/"):
                raw = raw.removeprefix("./").removeprefix("a/")
                line = "+++ b/" + raw
        normalized.append(line)
    text = "\n".join(normalized).strip()
    text = _canonicalize_hunk_counts(text)
    return text + "\n" if text else ""

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
            "files": [
                {
                    "path": f.path,
                    "content": PromptInjectionGuard.wrap(f.content, source=f.path, trust=TrustLevel.EXTERNAL),
                }
                for f in context.files
            ],
            "feedback": _redact(feedback)[:4000],
            "previous_summary": previous.summary if previous else "",
            "output_schema": {"unified_diff":"required real git-style unified diff", "file_contents":{"path":"optional complete UTF-8 file; may be {} because the adapter derives it from the diff"}, "summary":"string", "test_commands":["leave empty; sandbox validation executes the approved proposal commands"]},
            "diff_rule": "The unified_diff is the primary patch artifact. It must contain real '+++ b/<path>' and '@@' hunk lines. file_contents may be {}. Do not invent repository state beyond the supplied files.",
            "manifest_rule": "file_contents keys must be exact repository-relative paths from affected_area or the '+++ b/<path>' headers. Do not prefix keys with a/, b/, ./, or filesystem paths.",
            "validation_rule": "Do not invent, rewrite, or translate validation steps into commands. Leave test_commands empty so the sandbox executes only its approved proposal commands.",
            "constraints": ["Return JSON only.", "Never include secrets or private keys.", "Do not touch .git, .env, .github/workflows, or state/secrets.", "Do not merge, deploy, bill, or make external side effects."],
        }
        payload = {"model": self.config.model, "messages":[
            {"role":"system","content":"You are a constrained software engineer. Produce only a reviewable patch candidate."},
            {"role":"user","content":json.dumps(prompt, ensure_ascii=True)},
        ]}
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature
        if self.config.structured_output:
            # Use broadly compatible JSON mode for lightweight local providers.
            # The complete PatchCandidate schema is still supplied in the prompt,
            # then parsed and validated against the repository-side contract below.
            payload["response_format"] = {"type": "json_object"}
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
            if not isinstance(raw_files, dict) or not isinstance(test_commands, (list, tuple)):
                return None

            normalized_files = {}
            for key, value in raw_files.items():
                normalized = str(key).strip().replace("\\", "/").removeprefix("./")
                if normalized.startswith(("a/", "b/")):
                    normalized = normalized[2:]
                normalized_files[normalized] = str(value)
            unified_diff = _normalize_unified_diff(data.get("unified_diff", ""))

            # The unified diff is the primary patch artifact. When it is
            # reviewable against the already-bounded repository context, derive the
            # exact resulting file contents from that diff instead of trusting a
            # second model-generated manifest. This preserves the strict review
            # boundary while eliminating harmless manifest-shape drift.
            context_files = {item.path: item.content for item in context.files}
            materialized = None
            if unified_diff:
                materialized = materialize_patch_file_contents(unified_diff, context_files)
                if materialized is not None:
                    normalized_files = materialized

            # Some lightweight coding models emit a diff that looks unified but is
            # not safely materializable against the bounded base (for example, an
            # incorrect hunk body or context range) while still returning the
            # complete resulting file content. In that case, rebuild the diff from
            # only the explicitly affected, bounded repository files. This does not
            # widen the authority boundary: the rebuilt patch still goes through the
            # strict patch reviewer and exact file-content validation below.
            allowed_paths = {
                str(path).strip().replace("\\", "/").removeprefix("./")
                for path in proposal.affected_area
            }
            bounded_files = {
                path: content
                for path, content in normalized_files.items()
                if path in allowed_paths and path in context_files
            }
            if bounded_files and materialized is None:
                normalized_files = bounded_files

            # Some OpenAI-compatible coding models return the changed file correctly
            # but fail to format a valid unified diff. When the target file is present
            # in the bounded repository context, rebuild only that representation.
            # Patch review and validation remain the final authorities.
            if normalized_files and (
                materialized is None
                or "diff --git " not in unified_diff
                or "@@" not in unified_diff
            ):
                chunks: list[str] = []
                for path, new_content in normalized_files.items():
                    old_content = context_files.get(path)
                    if old_content is None:
                        chunks = []
                        break
                    diff_lines = list(
                        difflib.unified_diff(
                            old_content.splitlines(),
                            new_content.splitlines(),
                            fromfile=f"a/{path}",
                            tofile=f"b/{path}",
                            lineterm="",
                        )
                    )
                    if diff_lines:
                        chunks.append(f"diff --git a/{path} b/{path}")
                        chunks.extend(diff_lines)
                    else:
                        chunks = []
                        break
                if chunks:
                    unified_diff = "\n".join(chunks) + "\n"

            return PatchCandidate(
                unified_diff,
                normalized_files,
                str(data["summary"]),
                tuple(str(x) for x in test_commands),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
