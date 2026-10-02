from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable

from .provider_router import ProviderSpec


@dataclass(frozen=True)
class ProviderRoleRoute:
    role: str
    provider: ProviderSpec | None
    eligible: bool
    reason: str


class SpecialistProviderRouter:
    """Deterministically route specialist roles to configured provider routes.

    This is a selection layer only. It never stores credentials, enables paid
    fallback implicitly, or executes a provider request by itself.
    """

    def __init__(self, providers: Iterable[ProviderSpec], *, allow_paid: bool = False) -> None:
        self.providers = tuple(sorted(providers, key=lambda item: (item.priority, item.name)))
        self.allow_paid = bool(allow_paid)

    def route(self, role: str) -> ProviderRoleRoute:
        normalized_role = str(role).strip().lower() or "general"
        eligible: list[ProviderSpec] = []

        for provider in self.providers:
            capabilities = {str(value).strip().lower() for value in provider.capabilities}
            if normalized_role not in capabilities and "general" not in capabilities:
                continue
            cost = str(provider.cost_class).strip().lower()
            if cost == "paid" and not self.allow_paid:
                continue
            if cost != "free" and not (cost == "paid" and self.allow_paid):
                continue
            key_env = str(provider.config.api_key_env or "").strip()
            if not key_env or not os.getenv(key_env):
                continue
            eligible.append(provider)

        if not eligible:
            return ProviderRoleRoute(
                normalized_role,
                None,
                False,
                f"no eligible configured provider for specialist role {normalized_role}",
            )

        selected = eligible[0]
        return ProviderRoleRoute(
            normalized_role,
            selected,
            True,
            f"selected {selected.name} by deterministic priority/cost policy",
        )

    def routes(self, roles: Iterable[str]) -> tuple[ProviderRoleRoute, ...]:
        return tuple(self.route(role) for role in roles)


__all__ = ["ProviderRoleRoute", "SpecialistProviderRouter"]
