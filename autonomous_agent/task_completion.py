"""Validate requested deliverables separately from tool/approval success.

This module is an acceptance boundary, not a planner, provider, or source of
permissions. Untrusted file contents are data; they cannot change criteria,
select tools, grant access, or mark their own mission verified.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .cross_project_memory import _safe_text as _memory_safe_text
from .sandbox import SandboxResult
from .task_intent import positive_task_text

_READ_FILE = re.compile(
    r"\b(?:read|open|inspect|summari[sz]e|show)\s+(?:the\s+)?file\s+(?:at\s+)?"
    r"[\"'`]?([A-Za-z0-9_.-]+(?:[/\\][A-Za-z0-9_.-]+)*\.[A-Za-z0-9]{1,12})",
    re.IGNORECASE,
)
_READ_README = re.compile(
    r"\b(?:read|open)\s+(?:the\s+)?(?:temporary\s+)?readme(?:\.md)?\b",
    re.IGNORECASE,
)
_OUTPUT_VERB = re.compile(
    r"\b(?:summari[sz]e|summary|explain|describe|analy[sz]e|analysis|"
    r"review|report|assess|audit|tell|answer)\b",
    re.IGNORECASE,
)
_FACT_COUNT = re.compile(
    r"\b(?:summari[sz]e|summary|give|provide|list|state|extract|report|tell(?:\s+me)?)\b"
    r"[^.!?;]{0,100}?\b(one|two|three|four|five|[1-9][0-9]?)\s+"
    r"(?:distinct\s+|separate\s+)?facts?\b",
    re.IGNORECASE,
)
_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_INSTRUCTION = re.compile(
    r"^(?:ignore|disregard|do\s+not|don't|never|run|execute|send|delete|"
    r"write|you\s+must|assistant|system|instruction|prompt)\b",
    re.IGNORECASE,
)
MAX_FACTS = 5
MAX_FACT_LENGTH = 160
# A validated artifact must fit the existing persistent-memory string boundary.
MAX_ARTIFACT_LENGTH = 500


@dataclass(frozen=True)
class CompletionCriteria:
    """A bounded, user-derived deliverable contract (never an authorization)."""

    requires_output: bool
    fact_count: int | None = None
    source_path: str = ""

    @property
    def digest(self) -> str:
        payload = {"requires_output": self.requires_output, "fact_count": self.fact_count,
                   "source_path": self.source_path}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class CompletionAssessment:
    accepted: bool
    reason: str
    output: str = ""
    source_fingerprint: str = ""


def requested_read_path(task: str, root: Path | None = None) -> str:
    """Resolve an explicit local read; README shorthand means root README.md only."""
    positive = positive_task_text(task)
    # Routing text is lowercased; use it only to identify positive directives,
    # then recover the original path spelling for case-sensitive filesystems.
    allowed = {path.lower().replace("\\", "/") for path in _READ_FILE.findall(positive)}
    for match in _READ_FILE.finditer(task):
        path = match.group(1).replace("\\", "/")
        if path.lower() in allowed:
            return path
    if _READ_README.search(positive) and root is not None and (root / "README.md").is_file():
        return "README.md"
    return ""


def criteria_for(task: str, *, root: Path | None = None) -> CompletionCriteria:
    positive = positive_task_text(task)
    count_match = _FACT_COUNT.search(positive)
    requires_output = bool(_OUTPUT_VERB.search(positive) or count_match)
    count: int | None = None
    if count_match:
        value = count_match.group(1).lower()
        count = _NUMBERS.get(value, int(value) if value.isdigit() else 0)
    return CompletionCriteria(requires_output, count, requested_read_path(task, root))


def _source_statements(content: str) -> tuple[str, ...]:
    """Extract quoted, distinct statements; never execute instructions in text."""
    statements: list[str] = []
    seen: set[str] = set()
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "```", ">", "<!--")):
            continue
        line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line)
        for candidate in re.split(r"(?<=[.!?])\s+", line):
            statement = " ".join(candidate.split())
            if not (15 <= len(statement) <= MAX_FACT_LENGTH and statement.endswith((".", "!", "?"))):
                continue
            if _INSTRUCTION.match(statement) or not re.search(r"[A-Za-z]{2,}", statement):
                continue
            key = statement.casefold()
            if key not in seen:
                seen.add(key)
                statements.append(statement)
    return tuple(statements)


def assess_completion(
    criteria: CompletionCriteria,
    tool_names: Sequence[str],
    results: Sequence[SandboxResult],
    workspace_request: Mapping[str, object] | None = None,
) -> CompletionAssessment:
    """Require a source-backed output before a synthesis task can be VERIFIED.

    Only an explicit, bounded fact count over a verified local file read has a
    deterministic extractive implementation. Other summaries/analysis require
    a real synthesis path; selecting a provider or invoking a read is not one.
    """
    if not criteria.requires_output:
        return CompletionAssessment(True, "no written deliverable requested")
    count = criteria.fact_count
    if not count or count > MAX_FACTS or not criteria.source_path:
        return CompletionAssessment(False, "requested synthesis was not produced; no model-written analysis or validated output is available")
    if "filesystem.read" not in tool_names:
        return CompletionAssessment(False, "requested fact summary has no authorized file-read evidence")
    request = (workspace_request or {}).get("filesystem.read", {})
    if not isinstance(request, Mapping) or request.get("operation") != "read" or request.get("path") != criteria.source_path:
        return CompletionAssessment(False, "requested file does not match the authorized read request")
    for result in results:
        if (result.operation != "filesystem_workspace" or result.command != ("FILESYSTEM_WORKSPACE", "read")
                or not result.success or result.verification_status != "verified" or result.output_truncated):
            continue
        try:
            source = json.loads(result.output)
        except (TypeError, ValueError):
            continue
        if not isinstance(source, dict) or source.get("operation") != "read" or source.get("relative_path") != criteria.source_path:
            continue
        content, fingerprint = source.get("content"), source.get("fingerprint")
        if source.get("redacted") is not False or not isinstance(content, str) or not isinstance(fingerprint, str):
            continue
        # The workspace connector fingerprints the full original read. A
        # redacted/truncated/mismatched snapshot is not adequate source proof.
        expected = hashlib.sha256(json.dumps(
            {"path": criteria.source_path, "content": content},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str,
        ).encode()).hexdigest()
        if not hmac.compare_digest(fingerprint, expected):
            continue
        statements = _source_statements(content)
        if len(statements) < count:
            return CompletionAssessment(False, f"requested {count} facts, but only {len(statements)} distinct source statements could be validated")
        facts = statements[:count]
        output = (
            f"Extractive {count}-fact summary (source: {criteria.source_path}; SHA-256: {fingerprint[:12]}):\n"
            + "\n".join(f"{index}. {fact}" for index, fact in enumerate(facts, 1))
        )
        if len(output) > MAX_ARTIFACT_LENGTH:
            return CompletionAssessment(False, "source-backed summary exceeds the bounded output limit")
        if _memory_safe_text(output) != output:
            return CompletionAssessment(False, "source-backed summary contains unsafe or unpersistable text")
        return CompletionAssessment(True, f"validated {count} distinct extractive facts from the requested file", output, fingerprint)
    return CompletionAssessment(False, "requested summary lacks complete, unredacted, matching file-read evidence")
