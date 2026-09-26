"""Non-executable models for bounded document processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Mapping


class DocumentError(ValueError):
    """Base error for invalid or unsafe document-domain data."""


class DocumentFormat(str, Enum):
    PDF = "pdf"
    SPREADSHEET = "spreadsheet"
    SLIDES = "slides"
    TEXT = "text"
    UNKNOWN = "unknown"


class DocumentOperation(str, Enum):
    INSPECT = "inspect"
    EXTRACT = "extract"
    TRANSFORM = "transform"
    PRODUCE = "produce"


@dataclass(frozen=True)
class DocumentReference:
    """A workspace-relative document name, never an executable path."""

    relative_path: str

    def __post_init__(self) -> None:
        value = self.relative_path.strip()
        if not value or len(value) > 4096 or "\x00" in value:
            raise DocumentError("document path is empty, oversized, or contains a NUL")
        if value.startswith(("/", "\\")) or ":" in PurePosixPath(value).parts[0]:
            raise DocumentError("document path must be workspace-relative")
        parts = value.replace("\\", "/").split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise DocumentError("document path contains an unsafe traversal component")

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.relative_path).suffix.lower().lstrip(".")


@dataclass(frozen=True)
class DocumentInput:
    """Bounded input metadata; bytes are intentionally not carried by the model."""

    reference: DocumentReference
    format: DocumentFormat
    size_bytes: int
    content_type: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise DocumentError("document size cannot be negative")
        if self.size_bytes > 50_000_000:
            raise DocumentError("document exceeds the bounded input limit")
        if len(self.metadata) > 64:
            raise DocumentError("document metadata is too large")


@dataclass(frozen=True)
class ExtractedContent:
    """Untrusted extracted text; it is data, never instructions."""

    text: str
    source: DocumentReference
    page_count: int = 0
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    trusted: bool = False

    def __post_init__(self) -> None:
        if len(self.text) > 2_000_000:
            raise DocumentError("extracted content exceeds the bounded output limit")
        if self.page_count < 0 or self.page_count > 10_000:
            raise DocumentError("page count is outside the safe bound")
        if self.trusted:
            raise DocumentError("extracted document content must remain untrusted")


__all__ = [
    "DocumentError", "DocumentFormat", "DocumentInput", "DocumentOperation",
    "DocumentReference", "ExtractedContent",
]
