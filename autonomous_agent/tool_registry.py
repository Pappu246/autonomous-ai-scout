from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .capability_policy import Capability, CapabilityDecision, check_capability


@dataclass(frozen=True)
class ToolSpec:
    """Declarative tool metadata; registration never grants execution permission."""

    name: str
    capability: str
    description: str
    safe_autonomous: bool
    requires_approval: bool


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("github.inspect", Capability.INSPECT.value, "Read repository metadata and source state.", True, False),
    ToolSpec("filesystem.read", Capability.READ_FILE.value, "Read files from an approved workspace.", True, False),
    ToolSpec("tests.run", Capability.TEST.value, "Run the project's automated tests.", True, False),
    ToolSpec("lint.run", Capability.LINT.value, "Run configured static checks.", True, False),
    ToolSpec("metrics.collect", Capability.METRICS.value, "Collect deterministic project metrics.", True, False),
    ToolSpec("model.benchmark", Capability.BENCHMARK.value, "Benchmark an explicitly configured free model.", True, False),
    ToolSpec("network.fetch", Capability.NETWORK.value, "Fetch an explicitly requested network resource.", False, True),
    ToolSpec("github.change", Capability.SOURCE_WRITE.value, "Prepare an approved source change for review.", False, True),
    ToolSpec("github.merge", Capability.MERGE.value, "Merge a reviewed pull request.", False, True),
    ToolSpec("production.deploy", Capability.DEPLOY.value, "Deploy software to production.", False, True),
    ToolSpec("billing.manage", Capability.BILLING.value, "Change billing or paid-provider state.", False, True),
    ToolSpec("destructive.execute", Capability.DESTRUCTIVE.value, "Perform an irreversible or destructive action.", False, True),
)


_TOOL_INDEX = {spec.name: spec for spec in TOOL_SPECS}


def list_tools() -> tuple[ToolSpec, ...]:
    """Return the immutable declarative tool catalog."""
    return TOOL_SPECS


def get_tool(name: str) -> ToolSpec | None:
    """Resolve a tool without granting or executing it."""
    return _TOOL_INDEX.get(name.strip().lower()) if isinstance(name, str) else None


def authorize_tool(
    name: str,
    granted: Iterable[Capability | str] = (),
    *,
    explicitly_approved: bool = False,
) -> CapabilityDecision:
    """Check a tool against capability policy; registration itself never grants access."""
    spec = get_tool(name)
    if spec is None:
        return CapabilityDecision(False, "tool is not registered", "")
    decision = check_capability(spec.capability, granted)
    if not decision.allowed:
        return decision
    if spec.requires_approval and not explicitly_approved:
        return CapabilityDecision(False, "tool requires explicit approval", spec.capability)
    return CapabilityDecision(True, "tool is permitted by capability policy and approval state", spec.capability)
