import pytest

from autonomous_agent.capability_policy import Capability
from autonomous_agent.tool_registry import (
    ApprovalRequirement,
    AuditRequirement,
    AuthenticationRequirement,
    DuplicateToolError,
    NetworkRequirement,
    ReadWriteMode,
    RiskLevel,
    SandboxRequirement,
    ToolRegistry,
    ToolRegistryError,
    ToolSpec,
    authorize_tool,
    get_tool,
    list_tools,
    validate_tool_spec,
)


def test_every_builtin_has_complete_contract():
    required = (
        "name", "description", "category", "risk_level", "read_write_mode",
        "network_requirement", "authentication_requirement", "approval_requirement",
        "sandbox_requirement", "audit_requirement", "input_schema", "output_schema",
    )
    for tool in list_tools():
        for field in required:
            assert getattr(tool, field) not in (None, "")
        assert tool.input_schema["type"]
        assert tool.output_schema["type"]


def test_registration_validates_all_contract_fields():
    base = get_tool("tests.run")
    assert base is not None
    bad = ToolSpec(**{**base.__dict__, "input_schema": {"broken": True}})
    with pytest.raises(ToolRegistryError):
        validate_tool_spec(bad)


def test_duplicate_registration_is_deterministic_and_does_not_replace_existing():
    registry = ToolRegistry()
    original = registry.get("tests.run")
    assert original is not None
    with pytest.raises(DuplicateToolError):
        registry.register(original)
    assert registry.get("tests.run") is original


def test_unknown_tool_fails_closed():
    decision = authorize_tool("not.registered", [Capability.TEST])
    assert not decision.allowed
    assert "not registered" in decision.reason


def test_registration_never_grants_execution_permission():
    tool = get_tool("tests.run")
    assert tool is not None
    assert not authorize_tool(tool.name).allowed
    assert authorize_tool(tool.name, [Capability.TEST]).allowed


def test_capability_policy_remains_authoritative_even_with_approval():
    for name, capability in (
        ("github.merge", Capability.MERGE),
        ("production.deploy", Capability.DEPLOY),
        ("billing.manage", Capability.BILLING),
        ("payment.manage", Capability.BILLING),
        ("secrets.manage", Capability.SECRETS),
        ("destructive.execute", Capability.DESTRUCTIVE),
    ):
        decision = authorize_tool(name, [capability], explicitly_approved=True)
        assert not decision.allowed
        assert "permanently denied" in decision.reason


def test_high_risk_tools_require_audit_sandbox_and_approval():
    for name in ("github.change", "github.merge", "production.deploy", "billing.manage", "payment.manage", "secrets.manage", "destructive.execute"):
        tool = get_tool(name)
        assert tool is not None
        assert tool.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        assert tool.approval_requirement is not ApprovalRequirement.NONE
        assert tool.sandbox_requirement is SandboxRequirement.REQUIRED
        assert tool.audit_requirement is AuditRequirement.REQUIRED


def test_sandbox_requirement_is_enforced_before_authorization():
    decision = authorize_tool("tests.run", [Capability.TEST], sandbox_available=False)
    assert not decision.allowed
    assert "sandbox" in decision.reason


def test_audit_requirement_is_enforced_before_authorization():
    decision = authorize_tool("tests.run", [Capability.TEST], audit_available=False)
    assert not decision.allowed
    assert "audit" in decision.reason


def test_network_and_authentication_are_explicit_metadata():
    github = get_tool("github.inspect")
    files = get_tool("filesystem.read")
    model = get_tool("model.benchmark")
    assert github is not None and files is not None and model is not None
    assert github.network_requirement is NetworkRequirement.REQUIRED
    assert github.authentication_requirement is AuthenticationRequirement.USER_AUTH
    assert files.network_requirement is NetworkRequirement.NONE
    assert files.authentication_requirement is AuthenticationRequirement.NONE
    assert model.network_requirement is NetworkRequirement.REQUIRED
    assert model.authentication_requirement is AuthenticationRequirement.SERVICE_AUTH


def test_safe_autonomous_tools_are_read_only_and_non_approval():
    for tool in list_tools():
        if tool.safe_autonomous:
            assert tool.read_write_mode is ReadWriteMode.READ_ONLY
            assert tool.approval_requirement is ApprovalRequirement.NONE


def test_invalid_restricted_tool_cannot_be_registered_as_autonomous():
    base = get_tool("github.merge")
    assert base is not None
    bad = ToolSpec(**{**base.__dict__, "safe_autonomous": True})
    with pytest.raises(ToolRegistryError):
        ToolRegistry((bad,))


def test_catalog_has_unique_normalized_names():
    tools = list_tools()
    normalized = [tool.name.strip().lower() for tool in tools]
    assert len(normalized) == len(set(normalized))
