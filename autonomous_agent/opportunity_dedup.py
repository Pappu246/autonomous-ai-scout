from __future__ import annotations

import re

from .models import Opportunity


def _key(opportunity: Opportunity) -> str:
    """Build a stable, whitespace/punctuation-insensitive opportunity key."""
    text = f"{opportunity.title} {opportunity.next_step}".casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def deduplicate_opportunities(opportunities: list[Opportunity]) -> list[Opportunity]:
    """Return opportunities with duplicate recommendations collapsed deterministically.

    The first occurrence keeps its position; when duplicates are found, the highest
    score and the most informative description are retained. Input order is never
    mutated, and the returned list is safe for report/state serialization.
    """
    result: list[Opportunity] = []
    positions: dict[str, int] = {}

    for opportunity in opportunities:
        key = _key(opportunity)
        if not key:
            result.append(opportunity)
            continue

        existing_index = positions.get(key)
        if existing_index is None:
            positions[key] = len(result)
            result.append(opportunity)
            continue

        existing = result[existing_index]
        better = opportunity if opportunity.score > existing.score else existing
        description = max((existing.description, opportunity.description), key=len)
        result[existing_index] = better.model_copy(update={"description": description})

    return result
