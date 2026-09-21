from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from .ai_coding_brain import CodingModel, RepositoryContext
from .coding_provider import (
    ChatProviderConfig,
    OpenAICompatibleCodingModel,
    ProviderRequestError,
)
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
    """Bounded, fail-closed fallback across configured coding providers."""

    def __init__(
        self,
        providers: Iterable[ProviderSpec],
        *,
        allow_paid: bool = False,
        model_factory: Callable[[ChatProviderConfig], CodingModel] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retry_delay_seconds: float = 0.0,
    ):
        self.providers = tuple(
            sorted(providers, key=lambda item: (item.priority, item.name))
        )
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

    def generate_patch(
        self,
        *,
        proposal,
        context: RepositoryContext,
        feedback: str = "",
        previous: PatchCandidate | None = None,
    ):
        attempts: list[ProviderAttempt] = []

        for spec in self.providers:
            if not self._eligible(spec):
                attempts.append(
                    ProviderAttempt(
                        spec.name,
                        "skipped",
                        "ineligible_or_unconfigured",
                    )
                )
                continue

            model = self.model_factory(spec.config)
            attempt_limit = max(1, min(spec.max_attempts, 3))

            for attempt_number in range(attempt_limit):
                try:
                    candidate = model.generate_patch(
                        proposal=proposal,
                        context=context,
                        feedback=feedback,
                        previous=previous,
                    )
                except ProviderRequestError as exc:
                    attempts.append(
                        ProviderAttempt(
                            spec.name,
                            "failed",
                            str(exc)[:600],
                        )
                    )
                    candidate = None
                except Exception as exc:
                    attempts.append(
                        ProviderAttempt(
                            spec.name,
                            "failed",
                            type(exc).__name__,
                        )
                    )
                    candidate = None

                if isinstance(candidate, PatchCandidate):
                    attempts.append(
                        ProviderAttempt(spec.name, "succeeded")
                    )
                    self.last_attempts = tuple(attempts)
                    return candidate

                if not attempts or attempts[-1].provider != spec.name:
                    attempts.append(
                        ProviderAttempt(
                            spec.name,
                            "failed",
                            "no_valid_candidate",
                        )
                    )

                if (
                    attempt_number + 1 < attempt_limit
                    and self.retry_delay_seconds > 0
                ):
                    self.sleep(self.retry_delay_seconds)

        self.last_attempts = tuple(attempts)
        return None


def providers_from_env() -> tuple[ProviderSpec, ...]:
    """Build provider routes from explicit environment configuration.

    Credentials themselves are never stored. Only the environment-variable
    name containing the credential is stored.
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
            max_attempts = int(
                os.getenv(prefix + "MAX_ATTEMPTS", "1")
            )
            timeout = float(
                os.getenv(prefix + "TIMEOUT_SECONDS", "60")
            )
            raw_temperature = os.getenv(prefix + "TEMPERATURE", "").strip()
            temperature = float(raw_temperature) if raw_temperature else None
        except ValueError:
            continue

        cost_class = os.getenv(
            prefix + "COST_CLASS",
            "unknown",
        ).strip().lower()

        if cost_class not in {"free", "paid", "unknown"}:
            continue

        result.append(
            ProviderSpec(
                name=name,
                config=ChatProviderConfig(
                    endpoint=endpoint,
                    model=model,
                    api_key_env=api_key_env,
                    timeout_seconds=timeout,
                    temperature=temperature,
                ),
                priority=priority,
                cost_class=cost_class,
                max_attempts=max(1, min(max_attempts, 3)),
            )
        )

    return tuple(result)


def build_provider_router_from_env(
    *,
    allow_paid: bool = False,
) -> CodingProviderRouter:
    return CodingProviderRouter(
        providers_from_env(),
        allow_paid=allow_paid,
    )
