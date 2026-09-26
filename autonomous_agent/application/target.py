"""Semantic target resolution for the bounded application adapters domain.

Targets represent semantic controls or views inside an application adapter:
- active document
- view/panel name
- semantic control identifier

Coordinate-based UI automation is forbidden. Targets are bound to the
application observation epoch and fail closed if stale or foreign.
"""

from __future__ import annotations

from .models import (
    ApplicationObservation,
    ApplicationTarget,
    TargetResolutionError,
)


class ApplicationSemanticTargetResolver:
    """Resolve and validate semantic targets against application observations."""

    def resolve_view(
        self,
        observation: ApplicationObservation,
        view_name: str,
    ) -> ApplicationTarget:
        if observation is None:
            raise TargetResolutionError("application observation is required")
        cleaned_view = view_name.strip()
        if not cleaned_view:
            raise TargetResolutionError("view_name cannot be empty")

        available_views = observation.data.get("views", [])
        if cleaned_view not in available_views and observation.data.get("active_view") != cleaned_view:
            raise TargetResolutionError(
                f"view {cleaned_view!r} is not available in application {observation.app_id}"
            )

        return ApplicationTarget(
            target_id=f"view-{cleaned_view}",
            app_id=observation.app_id,
            document_path=observation.active_document,
            view_name=cleaned_view,
            epoch=observation.epoch,
        )

    def resolve_document(
        self,
        observation: ApplicationObservation,
        document_path: str,
    ) -> ApplicationTarget:
        if observation is None:
            raise TargetResolutionError("application observation is required")
        cleaned_path = document_path.strip()
        if not cleaned_path:
            raise TargetResolutionError("document_path cannot be empty")

        if observation.active_document and observation.active_document != cleaned_path:
            raise TargetResolutionError(
                f"document {cleaned_path!r} is not currently active (active: {observation.active_document!r})"
            )

        return ApplicationTarget(
            target_id=f"doc-{cleaned_path}",
            app_id=observation.app_id,
            document_path=cleaned_path,
            view_name=observation.data.get("active_view", ""),
            epoch=observation.epoch,
        )

    def ensure_current(
        self,
        target: ApplicationTarget,
        observation: ApplicationObservation,
    ) -> None:
        """Verify target belongs to current application observation epoch."""
        if target is None:
            raise TargetResolutionError("target is required")
        if observation is None:
            raise TargetResolutionError("observation is required")
        if target.app_id != observation.app_id:
            raise TargetResolutionError(
                f"target app_id {target.app_id!r} does not match current {observation.app_id!r}"
            )
        if target.epoch != observation.epoch:
            raise TargetResolutionError(
                "target is stale: it was resolved against a different application epoch"
            )


__all__ = ["ApplicationSemanticTargetResolver"]
