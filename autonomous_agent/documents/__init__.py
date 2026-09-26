"""Safe document-domain architecture.

M1 defines bounded data contracts and policy only. It intentionally contains no
parser, connector, or execution backend.
"""

from .models import (
    DocumentError,
    DocumentFormat,
    DocumentInput,
    DocumentOperation,
    DocumentReference,
    ExtractedContent,
)
from .policy import (
    DEFAULT_DOCUMENT_POLICY,
    DocumentPolicy,
    DocumentPolicyError,
)

__all__ = [
    "DEFAULT_DOCUMENT_POLICY",
    "DocumentError",
    "DocumentFormat",
    "DocumentInput",
    "DocumentOperation",
    "DocumentPolicy",
    "DocumentPolicyError",
    "DocumentReference",
    "ExtractedContent",
]
