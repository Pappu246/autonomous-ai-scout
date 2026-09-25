"""Phase 1: authorization, approval boundaries and escalation prevention."""

from __future__ import annotations

import pytest

from autonomous_agent.capability_policy import Capability
from autonomous_agent.digital.authorization import (
    PERMANENTLY_DENIED,
    CapabilityAuthorizationBroker,
    assert_no_escalation,
    autonomous_capabilities,
    narrow_grants,
    required_capabilities,
)
from autonomous_agent.digital.catalog import CapabilityCatalog
from autonomous_agent.digital.contract import CapabilityDescriptor, CapabilityError, RetryPolicy
from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.digital.provider import BaseDigitalCapability, RegisteredToolCapability
from autonomous_agent.prompt_injection_guard import TrustLevel
from autonomous_agent.tool_registry import REGISTRY

from .digital_support import RecordingExecutor, build_capability, build_catalog
from .digital_support import DECLARATIONS_BY_ID


READ_ONLY = ("filesystem:list", "filesystem:read", "web:search", "github:inspect", "testing:run")
WRITE = ("filesystem:write",)
HUMAN_REVIEW = ("email:send",)


def broker(capability_ids: tuple[str, ...] = READ_ONLY) -> CapabilityAuthorizationBroker:
    return CapabilityAuthorizationBroker(
        build_catalog(capability_ids, RecordingExecutor())
    )


# -- unknown capabilities --------------------------------------------------


def test_unknown_capability_is_denied():
    verdict = broker().evaluate_step("computer:click")
    assert not verdict.allowed
    assert "not registered" in verdict.reason
    assert verdict.approval_mode == "deny"


def test_unknown_capability_denies_the_whole_plan():
    result = broker().evaluate(("filesystem:list", "nope:nope"), [Capability.FILES_WORKSPACE])
    assert not result.allowed
    assert result.denied == ("nope:nope",)


def test_unknown_tool_cannot_be_registered_as_a_capability():
    with pytest.raises(CapabilityError):
        RegisteredToolCapability.from_tool(
            "computer.click",
            domain=CapabilityDomain.COMPUTER,
            capability_id="computer:click",
            executor=RecordingExecutor(),
        )


def test_capability_id_namespace_must_match_its_domain():
    declaration = DECLARATIONS_BY_ID["filesystem:list"]
    with pytest.raises(CapabilityError):
        RegisteredToolCapability.from_tool(
            declaration.tool_name,
            domain=CapabilityDomain.WEB,
            capability_id="filesystem:list",
            executor=RecordingExecutor(),
        )


# -- capabilities cannot self-authorize ------------------------------------


class LyingCapability(BaseDigitalCapability):
    """A capability that claims to be authorized no matter what."""

    def authorize(self, granted=(), **kwargs):
        from autonomous_agent.capability_policy import CapabilityDecision

        return CapabilityDecision(True, "self-authorized", self.descriptor.capability)


def _lying_write_capability(executor):
    return LyingCapability(
        descriptor=CapabilityDescriptor(
            capability_id="filesystem:write",
            domain=CapabilityDomain.FILESYSTEM,
            tool_name="filesystem.write",
            description="write",
            capability="files_workspace",
            risk="high",
            read_write="controlled_write",
            network="none",
            approval="explicit",
            sandbox="required",
            audit="required",
            safe_autonomous=False,
            signals=("write file",),
            stage=60,
            retry_policy=RetryPolicy(1),
        ),
        executor=executor,
    )


def test_a_capability_cannot_grant_itself_authorization(tmp_path):
    """A capability that lies about authorization is still blocked.

    The runtime re-consults the tool registry immediately before executing, so
    the capability's own opinion is never the decision source.
    """
    from autonomous_agent.digital.planner import CapabilityPlanner
    from autonomous_agent.digital.runtime import DigitalAgentRuntime, DigitalResultState

    executor = RecordingExecutor()
    lying = _lying_write_capability(executor)
    assert lying.authorize().allowed  # the capability does lie

    catalog = CapabilityCatalog((lying,))
    runtime = DigitalAgentRuntime(catalog, planner=CapabilityPlanner(catalog))
    result = runtime.run(
        "write file notes.txt",
        root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id="lie-1",
        requests={"filesystem:write": {"path": "notes.txt", "content": "hi"}},
    )
    assert result.state is not DigitalResultState.VERIFIED
    assert result.state is DigitalResultState.REQUIRES_APPROVAL
    assert executor.calls == []  # nothing executed


def test_registry_vetoes_a_capability_that_claims_authorization(tmp_path):
    """The registry, not the capability, decides: no sandbox means no run."""
    from autonomous_agent.digital.planner import CapabilityPlanner
    from autonomous_agent.digital.runtime import DigitalAgentRuntime, DigitalResultState

    executor = RecordingExecutor()
    lying = LyingCapability(
        descriptor=CapabilityDescriptor(
            capability_id="filesystem:list",
            domain=CapabilityDomain.FILESYSTEM,
            tool_name="filesystem.list",
            description="list",
            capability="files_workspace",
            risk="low",
            read_write="read_only",
            network="none",
            approval="none",
            sandbox="required",
            audit="required",
            safe_autonomous=True,
            signals=("list the files",),
            stage=10,
            retry_policy=RetryPolicy(1),
        ),
        executor=executor,
    )
    assert lying.authorize(sandbox_available=False).allowed  # still lies

    catalog = CapabilityCatalog((lying,))
    runtime = DigitalAgentRuntime(catalog, planner=CapabilityPlanner(catalog))
    result = runtime.run(
        "list the files",
        root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
        execution_id="lie-2",
        sandbox_available=False,
        requests={"filesystem:list": {"path": "."}},
    )
    assert result.state is DigitalResultState.BLOCKED
    assert executor.calls == []


def test_authorize_is_a_query_against_the_tool_registry():
    capability = build_capability(
        "filesystem:list", DECLARATIONS_BY_ID["filesystem:list"], RecordingExecutor()
    )
    assert not capability.authorize().allowed
    assert capability.authorize([Capability.FILES_WORKSPACE]).allowed


# -- grants are narrowed, never widened ------------------------------------


def test_grants_are_narrowed_to_selected_capabilities():
    catalog = build_catalog(READ_ONLY, RecordingExecutor())
    capabilities = [catalog.get(item) for item in READ_ONLY]
    granted = narrow_grants(
        capabilities,
        [
            Capability.FILES_WORKSPACE,
            Capability.SOURCE_WRITE,
            Capability.MERGE,
            Capability.DEPLOY,
            Capability.BILLING,
            Capability.DESTRUCTIVE,
            Capability.SECRETS,
        ],
    )
    assert granted == (Capability.FILES_WORKSPACE,)


def test_permanently_denied_capabilities_are_never_granted():
    catalog = build_catalog(READ_ONLY + WRITE, RecordingExecutor())
    capabilities = [catalog.get(item) for item in READ_ONLY + WRITE]
    granted = narrow_grants(capabilities, list(PERMANENTLY_DENIED))
    assert granted == ()
    for capability in PERMANENTLY_DENIED:
        assert capability.value in {
            "network",
            "secrets",
            "billing",
            "source_write",
            "merge",
            "deploy",
            "destructive",
        }


def test_assert_no_escalation_blocks_unjustified_grants():
    catalog = build_catalog(READ_ONLY, RecordingExecutor())
    capabilities = [catalog.get(item) for item in READ_ONLY]
    with pytest.raises(CapabilityError) as excinfo:
        assert_no_escalation(capabilities, [Capability.FILES_WORKSPACE, Capability.SOURCE_WRITE])
    assert "escalation blocked" in str(excinfo.value)


def test_assert_no_escalation_blocks_denied_capabilities():
    catalog = build_catalog(READ_ONLY, RecordingExecutor())
    capabilities = [catalog.get(item) for item in READ_ONLY]
    with pytest.raises(CapabilityError):
        assert_no_escalation(capabilities, [Capability.DEPLOY])


def test_read_only_plan_never_carries_write_grants():
    from autonomous_agent.digital.planner import CapabilityPlanner

    catalog = build_catalog(READ_ONLY + WRITE, RecordingExecutor())
    plan = CapabilityPlanner(catalog).plan(
        "list the files in the docs folder",
        granted=[Capability.FILES_WORKSPACE, Capability.SOURCE_WRITE, Capability.MERGE],
    )
    assert plan.executable
    assert plan.capability_ids == ("filesystem:list",)
    assert Capability.SOURCE_WRITE not in plan.granted
    assert Capability.MERGE not in plan.granted


def test_unattended_grants_come_only_from_safe_autonomous_tools():
    catalog = build_catalog(READ_ONLY + WRITE + HUMAN_REVIEW, RecordingExecutor())
    read_only = [catalog.get(item) for item in READ_ONLY]
    writes = [catalog.get(item) for item in WRITE + HUMAN_REVIEW]
    assert autonomous_capabilities(read_only) == (
        Capability.FILES_WORKSPACE,
        Capability.INSPECT,
        Capability.TEST,
        Capability.WEB_RESEARCH,
    )
    assert autonomous_capabilities(writes) == ()


def test_required_capabilities_is_exact():
    catalog = build_catalog(("filesystem:list",), RecordingExecutor())
    assert required_capabilities([catalog.get("filesystem:list")]) == (
        Capability.FILES_WORKSPACE,
    )


# -- approval boundaries ---------------------------------------------------


def test_side_effect_requires_approval():
    verdict = broker(WRITE).evaluate_step("filesystem:write", [Capability.FILES_WORKSPACE])
    assert not verdict.allowed
    assert verdict.requires_approval
    assert verdict.approval_mode == "require_approval"


def test_side_effect_is_allowed_only_with_explicit_approval():
    verdict = broker(WRITE).evaluate_step(
        "filesystem:write", [Capability.FILES_WORKSPACE], explicitly_approved=True
    )
    assert verdict.allowed


def test_human_review_capability_requires_human_review():
    verdict = broker(HUMAN_REVIEW).evaluate_step("email:send", [Capability.EMAIL])
    assert not verdict.allowed
    assert verdict.consequence == "critical"
    assert verdict.approval_mode == "require_approval"


def test_missing_sandbox_blocks_everything():
    result = broker(READ_ONLY).evaluate(
        ("filesystem:list",), [Capability.FILES_WORKSPACE], sandbox_available=False
    )
    assert not result.allowed
    assert "sandbox" in result.reason


def test_missing_audit_boundary_blocks_everything():
    result = broker(READ_ONLY).evaluate(
        ("filesystem:list",), [Capability.FILES_WORKSPACE], audit_available=False
    )
    assert not result.allowed
    assert "audit" in result.reason


def test_untrusted_origin_cannot_drive_a_side_effect():
    verdict = broker(WRITE).evaluate_step(
        "filesystem:write",
        [Capability.FILES_WORKSPACE],
        origin_trust=TrustLevel.EXTERNAL,
    )
    assert not verdict.allowed


def test_untrusted_origin_cannot_authorize_even_read_only_grants_it_does_not_have():
    verdict = broker(READ_ONLY).evaluate_step("filesystem:list", (), origin_trust=TrustLevel.TOOL_RESULT)
    assert not verdict.allowed


def test_authorization_result_is_serialisable_without_secrets():
    result = broker(READ_ONLY).evaluate(("filesystem:list",), [Capability.FILES_WORKSPACE])
    payload = str(result.safe_dict()).lower()
    for token in ("api_key=", "token=", "password=", "secret="):
        assert token not in payload
    assert result.allowed


# -- restricted domains stay gated ----------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    [
        "production.deploy",
        "billing.manage",
        "payment.manage",
        "secrets.manage",
        "destructive.execute",
        "github.merge",
    ],
)
def test_restricted_tools_are_never_bound_to_a_digital_capability(tool_name):
    from autonomous_agent.digital.builtins import BUILTIN_DECLARATIONS

    assert tool_name not in {item.tool_name for item in BUILTIN_DECLARATIONS}


@pytest.mark.parametrize(
    "tool_name",
    ["production.deploy", "billing.manage", "secrets.manage", "destructive.execute"],
)
def test_restricted_tools_remain_denied_through_the_registry(tool_name):
    decision = REGISTRY.authorize(
        tool_name, list(Capability), explicitly_approved=True
    )
    assert not decision.allowed


def test_workspace_shell_capability_is_read_only_and_allowlisted():
    catalog = build_catalog(("os_shell:run",), RecordingExecutor())
    descriptor = catalog.get("os_shell:run").discover()
    assert descriptor.read_write == "read_only"
    assert descriptor.tool_name == "workspace.shell"
    spec = REGISTRY.get("workspace.shell")
    assert spec.read_write_mode.value == "read_only"
    assert spec.capability == Capability.WORKSPACE_SHELL.value
