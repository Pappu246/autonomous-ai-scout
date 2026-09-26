"""Core models, bounds, data shapes, untrusted content wrapping, secret redaction, and fail-closed exceptions for bounded document processing.

Nothing in this module invokes external document rendering engines, Office suites,
or live execution environments. It defines pure Python data structures, bounds,
and security types for document inspection and transformation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


# --------------------------------------------------------------------------
# Exceptions -- fail-closed signals for document processing domain.
# --------------------------------------------------------------------------
class DocumentError(Exception):
    """Base exception for the document processing domain."""


class DocumentSecurityError(DocumentError):
    """Raised when a document action violates safety boundaries or security policy."""


class DocumentFormatError(DocumentError):
    """Raised when a document format is invalid, corrupted, or unsupported."""


class DocumentNotFoundError(DocumentError):
    """Raised when a specified document is not found."""


class DocumentExtractionError(DocumentError):
    """Raised when bounded text or table extraction fails."""


class DocumentTransformError(DocumentError):
    """Raised when a document transformation operation fails."""


class ActionBudgetExceededError(DocumentError):
    """Raised when an operation attempts to exceed the allocated document action budget."""


# --------------------------------------------------------------------------
# Bounds -- upper limits to prevent resource exhaustion and decompression bombs.
# --------------------------------------------------------------------------
MAX_DOCUMENT_BYTES = 25_000_000          # 25 MB max document file size
MAX_EXTRACTED_TEXT_LENGTH = 100_000      # 100k chars for extracted text evidence
MAX_PAGE_COUNT = 1000                    # max pages to prevent infinite page loops
MAX_PAGE_TEXT_LENGTH = 16_384           # max chars per page
MAX_TABLE_ROWS = 10_000
MAX_TABLE_COLUMNS = 200
MAX_FORM_FIELDS = 500
MAX_ACTION_BUDGET = 50
MAX_INPUT_DOCUMENTS = 20


# --------------------------------------------------------------------------
# Credential material -- detection and redaction
# --------------------------------------------------------------------------
_CREDENTIAL_MATERIAL = re.compile(
    r"(?i)((?:\bapi[_-]?key\b|\baccess[_-]?token\b|\brefresh[_-]?token\b|\btoken\b|"
    r"\bauthorization\b|\bpassword\b|\bpasswd\b|\bsecret\b|\bprivate[_-]?key\b|"
    r"\bclient[_-]?secret\b|\bcredentials?\b)\s*[:=]\s*)\S+"
)
_BEARER_MATERIAL = re.compile(r"(?i)(bearer\s+)\S+")
REDACTED = "[REDACTED]"


def looks_like_secret(text: str) -> bool:
    """True when text contains credential material or a bearer token."""
    if not isinstance(text, str) or not text:
        return False
    return bool(_CREDENTIAL_MATERIAL.search(text) or _BEARER_MATERIAL.search(text))


def redact_secret(text: str) -> str:
    """Strip credential material from text so it never reaches model/audit context."""
    if not isinstance(text, str):
        return text
    stripped = _BEARER_MATERIAL.sub(r"\1" + REDACTED, text)
    return _CREDENTIAL_MATERIAL.sub(r"\1" + REDACTED, stripped)


def wrap_untrusted_document_content(content: str) -> str:
    """Wrap untrusted document content in clear boundary markers to guard against prompt injection."""
    if not isinstance(content, str):
        return ""
    safe_content = redact_secret(content)
    return f"--- BEGIN UNTRUSTED DOCUMENT CONTENT ---\n{safe_content}\n--- END UNTRUSTED DOCUMENT CONTENT ---"


# --------------------------------------------------------------------------
# Consequential action detection
# --------------------------------------------------------------------------
_CONSEQUENTIAL_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\bdigital signature\b|\bsign document\b|\bcertify\b|\be-sign\b",
        r"\bdecrypt\b|\bunlock password\b|\bremove password\b|\bexecute macro\b|\brun vba\b",
        r"\bdelete all pages\b|\bwipe document\b|\bpurge content\b",
        r"\boverwrite original\b|\bdestructive edit\b",
    )
)


def consequential_signal(*texts: str) -> str:
    """Return a human-readable reason if any visible text implies a high-impact document action."""
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        for pattern in _CONSEQUENTIAL_PATTERNS:
            if pattern.search(text):
                return f"consequential document action signal: {pattern.pattern}"
    return ""


# --------------------------------------------------------------------------
# Enums and Data Shapes
# --------------------------------------------------------------------------
class DocumentType(str, Enum):
    PDF = "pdf"
    SPREADSHEET = "spreadsheet"
    DOCUMENT = "document"
    PRESENTATION = "presentation"
    TEXT = "text"
    CSV = "csv"
    UNKNOWN = "unknown"


class DocumentOperationType(str, Enum):
    READ = "read"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_TABLES = "extract_tables"
    EXTRACT_METADATA = "extract_metadata"
    MERGE = "merge"
    SPLIT = "split"
    CONVERT = "convert"
    TRANSFORM = "transform"


@dataclass(frozen=True)
class DocumentMetadata:
    """Bounded, safe metadata for a document."""

    filename: str
    relative_path: str
    document_type: DocumentType
    size_bytes: int
    sha256: str = ""
    page_count: int = 0
    title: str = ""
    author: str = ""
    is_encrypted: bool = False
    has_macros: bool = False

    def safe_dict(self) -> dict[str, Any]:
        return {
            "filename": redact_secret(self.filename)[:128],
            "relative_path": redact_secret(self.relative_path)[:1024],
            "document_type": self.document_type.value if isinstance(self.document_type, DocumentType) else str(self.document_type),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "page_count": self.page_count,
            "title": redact_secret(self.title)[:256],
            "author": redact_secret(self.author)[:128],
            "is_encrypted": self.is_encrypted,
            "has_macros": self.has_macros,
        }


@dataclass(frozen=True)
class DocumentPage:
    """A bounded view of one document page."""

    page_number: int
    text: str = ""
    table_count: int = 0
    image_count: int = 0
    form_fields: tuple[str, ...] = ()

    def safe_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "text": redact_secret(self.text)[:MAX_PAGE_TEXT_LENGTH],
            "table_count": self.table_count,
            "image_count": self.image_count,
            "form_fields": tuple(redact_secret(f)[:128] for f in self.form_fields[:MAX_FORM_FIELDS]),
        }


@dataclass(frozen=True)
class DocumentTable:
    """A bounded representation of an extracted tabular structure."""

    table_id: str
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    page_number: int | None = None

    def safe_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "headers": tuple(redact_secret(h)[:128] for h in self.headers[:MAX_TABLE_COLUMNS]),
            "rows": [
                [redact_secret(cell)[:256] for cell in row[:MAX_TABLE_COLUMNS]]
                for row in self.rows[:MAX_TABLE_ROWS]
            ],
            "page_number": self.page_number,
        }


@dataclass(frozen=True)
class DocumentObservation:
    """A bounded snapshot of an inspected document."""

    relative_path: str
    document_type: DocumentType
    metadata: DocumentMetadata
    extracted_text: str = ""
    pages: tuple[DocumentPage, ...] = ()
    tables: tuple[DocumentTable, ...] = ()
    warnings: tuple[str, ...] = ()
    epoch: int = 0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "relative_path": redact_secret(self.relative_path)[:1024],
            "document_type": self.document_type.value if isinstance(self.document_type, DocumentType) else str(self.document_type),
            "metadata": self.metadata.safe_dict(),
            "extracted_text": redact_secret(self.extracted_text)[:MAX_EXTRACTED_TEXT_LENGTH],
            "pages": [p.safe_dict() for p in self.pages[:MAX_PAGE_COUNT]],
            "tables": [t.safe_dict() for t in self.tables[:100]],
            "warnings": list(self.warnings[:50]),
            "epoch": self.epoch,
        }


@dataclass(frozen=True)
class DocumentTransformRequest:
    """A bounded specification for transforming documents."""

    operation: DocumentOperationType
    input_paths: tuple[str, ...]
    output_path: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def safe_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value if isinstance(self.operation, DocumentOperationType) else str(self.operation),
            "input_paths": list(self.input_paths[:MAX_INPUT_DOCUMENTS]),
            "output_path": self.output_path,
        }


@dataclass(frozen=True)
class DocumentTransformResult:
    """Result of a completed, verified document transformation."""

    operation: DocumentOperationType
    output_path: str
    size_bytes: int
    sha256: str
    page_count: int = 0
    verified: bool = False

    def safe_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value if isinstance(self.operation, DocumentOperationType) else str(self.operation),
            "output_path": self.output_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "page_count": self.page_count,
            "verified": self.verified,
        }


@dataclass
class ActionBudget:
    """Enforces an upper bound on document operations in a session."""

    limit: int = MAX_ACTION_BUDGET
    used: int = 0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("action budget limit must be positive")

    def consume(self, count: int = 1) -> None:
        if count <= 0:
            raise ValueError("action count must be positive")
        if self.used + count > self.limit:
            raise ActionBudgetExceededError(
                f"document action budget exceeded: attempted {self.used + count}, limit is {self.limit}"
            )
        self.used += count

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def reset(self) -> None:
        self.used = 0


__all__ = [
    "ActionBudget",
    "ActionBudgetExceededError",
    "DocumentError",
    "DocumentExtractionError",
    "DocumentFormatError",
    "DocumentMetadata",
    "DocumentNotFoundError",
    "DocumentObservation",
    "DocumentOperationType",
    "DocumentPage",
    "DocumentSecurityError",
    "DocumentTable",
    "DocumentTransformError",
    "DocumentTransformRequest",
    "DocumentTransformResult",
    "DocumentType",
    "MAX_ACTION_BUDGET",
    "MAX_DOCUMENT_BYTES",
    "MAX_EXTRACTED_TEXT_LENGTH",
    "MAX_FORM_FIELDS",
    "MAX_INPUT_DOCUMENTS",
    "MAX_PAGE_COUNT",
    "MAX_PAGE_TEXT_LENGTH",
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_ROWS",
    "REDACTED",
    "consequential_signal",
    "looks_like_secret",
    "redact_secret",
    "wrap_untrusted_document_content",
]
