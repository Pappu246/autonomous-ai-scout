"""Semantic target resolution for the bounded browser agent.

Targets are resolved by *meaning*, never by raw coordinates:

- ARIA role
- accessible name
- visible text
- a stable selector

A resolved :class:`~autonomous_agent.browser.models.ElementTarget` is bound to
the page epoch it was resolved against. Re-using a target after the page has
changed (a stale target) or a target that never belonged to the current page
(a foreign target) fails closed via :class:`TargetResolutionError`. There is
deliberately **no** coordinate-based fallback: an interaction that cannot be
justified by a live, semantic match is refused rather than guessed.
"""

from __future__ import annotations

from .models import (
    ElementTarget,
    PageElement,
    PageObservation,
    TargetResolutionError,
)


class SemanticTargetResolver:
    """Resolve and validate semantic element targets against the current page."""

    def resolve(
        self,
        page: PageObservation,
        *,
        role: str = "",
        accessible_name: str = "",
        visible_text: str = "",
        selector: str = "",
    ) -> ElementTarget:
        """Resolve exactly one target on ``page`` or fail closed.

        Resolution is deterministic: the first element that matches all provided
        non-empty criteria wins. Ambiguous or absent matches raise.
        """
        if page is None:
            raise TargetResolutionError("no page is loaded to resolve a target against")
        role_q = (role or "").strip().lower()
        name_q = (accessible_name or "").strip().lower()
        text_q = (visible_text or "").strip().lower()
        sel_q = (selector or "").strip()

        if not (role_q or name_q or text_q or sel_q):
            raise TargetResolutionError("a target requires role, accessible name, text or selector")

        matches: list[PageElement] = []
        for element in page.elements:
            if element.disabled:
                continue
            if sel_q and element.selector != sel_q:
                continue
            if role_q and element.role.strip().lower() != role_q:
                continue
            if name_q and element.accessible_name.strip().lower() != name_q:
                continue
            if text_q and element.visible_text.strip().lower() != text_q:
                continue
            matches.append(element)

        if not matches:
            raise TargetResolutionError("no element matches the requested target on the current page")
        if len(matches) > 1:
            raise TargetResolutionError(
                f"target is ambiguous: {len(matches)} elements match; refine the query"
            )
        return self._bind(matches[0], page.epoch)

    def _bind(self, element: PageElement, epoch: int) -> ElementTarget:
        return ElementTarget(
            element_id=element.element_id,
            selector=element.selector,
            role=element.role,
            accessible_name=element.accessible_name,
            epoch=epoch,
        )

    def ensure_current(self, target: ElementTarget, page: PageObservation) -> PageElement:
        """Confirm ``target`` still belongs to ``page`` and is not stale.

        Fails closed when the page epoch has moved on or the target's element is
        no longer present. Never falls back to coordinates.
        """
        if target is None:
            raise TargetResolutionError("a target is required")
        if page is None:
            raise TargetResolutionError("no page is loaded")
        if target.epoch != page.epoch:
            raise TargetResolutionError(
                "target is stale: it was resolved against a different page state"
            )
        for element in page.elements:
            if element.element_id == target.element_id and element.selector == target.selector:
                return element
        raise TargetResolutionError("target element is not present on the current page")


__all__ = ["SemanticTargetResolver"]
