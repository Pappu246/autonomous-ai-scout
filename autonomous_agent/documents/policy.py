"""Fail-closed policy primitives for the document domain.

This module deliberately does not open, parse, or execute documents.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import DocumentFormat, DocumentInput, DocumentOperation


class DocumentPolicyError(ValueError):
    """Raised when a document request crosses a safety boundary."""


@dataclass(frozen=True)
class DocumentPolicy:
    max_input_bytes: int = 50_000_000
    max_extracted_chars: int = 2_000_000
    allowed_formats: frozenset[DocumentFormat] = frozenset({
        DocumentFormat.PDF, DocumentFormat.SPREADSHEET,
        DocumentFormat.SLIDES, DocumentFormat.TEXT,
    })
    allow_active_content: bool = False
    allow_executable_content: bool = False
    require_workspace: bool = True

    def validate(self, document: DocumentInput, workspace: Path | str) -> Path:
        """Validate confinement and bounds, returning the confined path only."""
        if document.format not in self.allowed_formats or document.format is DocumentFormat.UNKNOWN:
            raise DocumentPolicyError("unsupported document format is denied")
        if document.size_bytes > self.max_input_bytes:
            raise DocumentPolicyError("document input exceeds policy limit")
        if not self.require_workspace:
            raise DocumentPolicyError("workspace confinement cannot be disabled")
        root = Path(workspace).resolve()
        candidate = (root / document.reference.relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DocumentPolicyError("document path escapes the workspace") from exc
        return candidate

    def allows(self, operation: DocumentOperation, *, approved: bool = False) -> bool:
        """Read-only inspection/extraction may be planned; writes need approval."""
        if operation in {DocumentOperation.TRANSFORM, DocumentOperation.PRODUCE}:
            return bool(approved)
        return True


DEFAULT_DOCUMENT_POLICY = DocumentPolicy()

__all__ = ["DEFAULT_DOCUMENT_POLICY", "DocumentPolicy", "DocumentPolicyError"]
