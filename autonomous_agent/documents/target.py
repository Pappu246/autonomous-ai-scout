"""Semantic target resolution for the bounded documents domain.

Targets represent semantic locations in a document:
- specific page
- extracted table
- section or text match

Targets are bound to the document observation epoch and fail closed if stale or foreign.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    DocumentObservation,
    DocumentPage,
    DocumentTable,
    TargetResolutionError,
    redact_secret,
)


@dataclass(frozen=True)
class DocumentTarget:
    """A resolved semantic target bound to a document observation epoch."""

    target_id: str
    relative_path: str
    target_type: str  # "page", "table", "text"
    page_number: int | None = None
    identifier: str = ""
    epoch: int = 0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "relative_path": self.relative_path,
            "target_type": self.target_type,
            "page_number": self.page_number,
            "identifier": redact_secret(self.identifier)[:128],
            "epoch": self.epoch,
        }


class SemanticDocumentTargetResolver:
    """Resolve and validate semantic document targets against observations."""

    def resolve_page(self, observation: DocumentObservation, page_number: int) -> DocumentTarget:
        if observation is None:
            raise TargetResolutionError("document observation is required")
        if page_number < 1:
            raise TargetResolutionError("page number must be >= 1")
        for p in observation.pages:
            if p.page_number == page_number:
                return DocumentTarget(
                    target_id=f"page-{page_number}",
                    relative_path=observation.relative_path,
                    target_type="page",
                    page_number=page_number,
                    identifier=f"page_{page_number}",
                    epoch=observation.epoch,
                )
        raise TargetResolutionError(
            f"page {page_number} does not exist in {observation.relative_path}"
        )

    def resolve_table(self, observation: DocumentObservation, table_id: str) -> DocumentTarget:
        if observation is None:
            raise TargetResolutionError("document observation is required")
        cleaned_id = table_id.strip()
        if not cleaned_id:
            raise TargetResolutionError("table_id cannot be empty")
        for t in observation.tables:
            if t.table_id == cleaned_id:
                return DocumentTarget(
                    target_id=f"table-{cleaned_id}",
                    relative_path=observation.relative_path,
                    target_type="table",
                    page_number=t.page_number,
                    identifier=cleaned_id,
                    epoch=observation.epoch,
                )
        raise TargetResolutionError(
            f"table {cleaned_id} does not exist in {observation.relative_path}"
        )

    def ensure_current(
        self,
        target: DocumentTarget,
        observation: DocumentObservation,
    ) -> DocumentPage | DocumentTable:
        """Verify target belongs to the current observation epoch."""
        if target is None:
            raise TargetResolutionError("target is required")
        if observation is None:
            raise TargetResolutionError("observation is required")
        if target.relative_path != observation.relative_path:
            raise TargetResolutionError(
                f"target path {target.relative_path} does not match current document {observation.relative_path}"
            )
        if target.epoch != observation.epoch:
            raise TargetResolutionError(
                "target is stale: it was resolved against a different document epoch"
            )
        if target.target_type == "page" and target.page_number is not None:
            for p in observation.pages:
                if p.page_number == target.page_number:
                    return p
            raise TargetResolutionError(f"target page {target.page_number} is no longer present")
        elif target.target_type == "table":
            for t in observation.tables:
                if t.table_id == target.identifier:
                    return t
            raise TargetResolutionError(f"target table {target.identifier} is no longer present")
        raise TargetResolutionError(f"unsupported target type: {target.target_type}")


__all__ = ["DocumentTarget", "SemanticDocumentTargetResolver"]
