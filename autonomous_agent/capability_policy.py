from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class Capability(str, Enum):
    INSPECT = "inspect"
    TEST = "test"
    LINT = "lint"
    METRICS = "metrics"
    READ_FILE = "read_file"
    BENCHMARK = "benchmark"
    WEB_RESEARCH = "web_research"
    NETWORK = "network"
    SECRETS = "secrets"
    BILLING = "billing"
    SOURCE_WRITE = "source_write"
    MERGE = "merge"
    DEPLOY = "deploy"
    DESTRUCTIVE = "destructive"


SAFE_CAPABILITIES = frozenset({
    Capability.INSPECT,
    Capability.TEST,
    Capability.LINT,
    Capability.METRICS,
    Capability.READ_FILE,
    Capability.BENCHMARK,
    Capability.WEB_RESEARCH,
})
DENIED_CAPABILITIES = frozenset(set(Capability) - SAFE_CAPABILITIES)


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    reason: str
    capability: str


def normalize_capability(value: Capability | str) -> str:
    if isinstance(value, Capability):
        return value.value
    if not isinstance(value, str):
        raise ValueError("capability must be a string")
    normalized = value.strip().lower()
    if normalized not in {item.value for item in Capability}:
        raise ValueError("unknown capability")
    return normalized


def check_capability(value: Capability | str, granted: Iterable[Capability | str] = ()) -> CapabilityDecision:
    try:
        capability = normalize_capability(value)
        granted_normalized = {normalize_capability(item) for item in granted}
    except (TypeError, ValueError):
        return CapabilityDecision(False, "capability input is invalid", "")
    if capability in {item.value for item in DENIED_CAPABILITIES}:
        return CapabilityDecision(False, "capability is permanently denied by autonomous policy", capability)
    if capability not in granted_normalized:
        return CapabilityDecision(False, "capability has not been explicitly granted", capability)
    return CapabilityDecision(True, "capability is explicitly granted and within the safe allowlist", capability)
