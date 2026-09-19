from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from .ai_coding_brain import CodingModel, RepositoryContext
from .coding_provider import ChatProviderConfig, OpenAICompatibleCodingModel
from .self_improvement import PatchCandidate


@dataclass(frozen=True)
class ProviderSpec:
    """Explicitly configured coding-provider route."""

    name: str
    config: ChatProviderConfig
    priority: int = 100
    cost_class: str = "unknown"
    capabilities: tuple[str, ...] = ("coding",)
    max_attempts: int = 1


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    status: str
    detail: str = ""


class CodingProviderRouter(CodingModel):
    """Fail-closed, bounded fallback across explicitly configured providers.

    The router never persists credentials and never enables a paid route unless
    allow_paid=True. Provider failures are isolated and fallback is bounded.
    """

    def __init__(
        self,
        providers: Iterable[ProviderSpec],
        *,
        allow_paid: bool = False,
        model_factory: Callable[[ChatProviderConfig], CodingModel] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retry_delay_seconds: float = 0.0,
    ):
        self.providers = tuple(sorted(providers, key=lambda item: (item.priority, item.name)))
        self.allow_paid = allow_paid
        self.model_factory = model_factory or OpenAICompatibleCodingModel
        self.sleep = sleep
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.last_attempts: tuple[ProviderAttempt, ...] = ()

    def _eligible(self, spec: ProviderSpec) -> bool:
        if "coding" not in spec.capabilities:
            return False
        if spec.cost_class == "paid" and not self.allow_paid:
            return False
        return bool(os.getenv(spec.config.api_key_env))

    def generate_patch(self, *, proposal, context: RepositoryContext, feedback="", previous=None):
        attempts: list[ProviderAttempt] = []
        for spec in self.providers:
            if not self._eligible(spec):
                attempts.append(ProviderAttempt(spec.name, "skipped", "ineligible_or_unconfigured"))
                continue
            model = self.model_factory(spec.config)
            for attempt_number in range(max(1, min(spec.max_attempts, 3))):
                try:
                    candidate = model.generate_patch(
                        proposal=proposal,
                        context=context,
                        feedback=feedback,
                        previous=previous,
                    )
                except Exception as exc:
                    attempts.append(ProviderAttempt(spec.name, "failed", type(exc).__name__))
                    candidate = None
                if isinstance(candidate, PatchCandidate):
                    attempts.append(ProviderAttempt(spec.name, "succeeded"))
                    self.last_attempts = tuple(attempts)
                    return candidate
                if not attempts or attempts[-1].provider != spec.name or attempts[-1].status != "failed":
                    attempts.append(ProviderAttempt(spec.name, "failed", "no_valid_candidate"))
                if attempt_number + 1 < max(1, min(spec.max_attempts, 3)) and self.retry_delay_seconds:
                    self.sleep(self.retry_delay_seconds)
        self.last_attempts = tuple(attempts)
        return None


def providers_from_env() -> tuple[ProviderSpec, ...]:
    """Build only routes explicitly enabled by environment configuration.

    Required variables per route:
      CODING_PROVIDER_<N>_NAME
      CODING_PROVIDER_<N>_ENDPOINT
      CODING_PROVIDER_<N>_MODEL
      CODING_PROVIDER_<N>_API_KEY_ENV

    Optional:
      ..._COST_CLASS = free|paid|unknown
      ..._PRIORITY
      ..._MAX_ATTEMPTS
      ..._TIMEOUT_SECONDS

    This intentionally avoids silently inventing provider endpoints/models.
    """

    result: list[ProviderSpec] = []
    for index in range(1, 9):
        prefix = f"CODING_PROVIDER_{index}_"
        name = os.getenv(prefix + "NAME", "").strip()
        endpoint = os.getenv(prefix + "ENDPOINT", "").strip()
        model = os.getenv(prefix + "MODEL", "").strip()
        api_key_env = os.getenv(prefix + "API_KEY_ENV", "").strip()
        if not any((name, endpoint, model, api_key_env)):
            continue
        if not all((name, endpoint, model, api_key_env)):
            continue
        try:
            priority = int(os.getenv(prefix + "PRIORITY", "100"))
            max_attempts = int(os.getenv(prefix + "MAX_ATTEMPTS", "1"))
            timeout = float(os.getenv(prefix + "TIMEOUT_SECONDS", "60"))
        except ValueError:
            continue
        cost_class = os.getenv(prefix + "COST_CLASS", "unknown").strip().lower()
        if cost_class not in {"free", "paid", "unknown"}:
            continue
        result.append(
            ProviderSpec(
                name=name,
                config=ChatProviderConfig(endpoint, model, api_key_env, timeout),
                priority=priority,
                cost_class=cost_class,
                max_attempts=max(1, min(max_attempts, 3)),
            )
        )
    return tuple(result)


def build_provider_router_from_env(*, allow_paid: bool = False) -> CodingProviderRouter:
    return CodingProviderRouter(providers_from_env(), allow_paid=allow_paid)
