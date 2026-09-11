import pytest

from autonomous_agent.capability_policy import Capability
from autonomous_agent.connector_adapter import ConnectorAdapter
from autonomous_agent.connector_registry import (
    BUILTIN_CONNECTORS,
    ConnectorAuth,
    ConnectorRegistry,
    ConnectorRegistryError,
    ConnectorSpec,
    CredentialHandling,
    get_connector,
)
from autonomous_agent.tool_registry import (
    ApprovalRequirement,
    AuditRequirement,
    NetworkRequirement,
    ReadWriteMode,
    RiskLevel,
    SandboxRequirement,
    REGISTRY,
    ToolRegistry,
)


def spec(**overrides):
    base = {
        "identity": "custom",
        "description": "Custom connector.",
        "category": "test",
        "capabilities": (Capability.INSPECT.value,),
        "scopes": ("read",),
        "authentication_method": ConnectorAuth.USER_AUTH,
        "credential_handling": CredentialHandling.REFERENCE_ONLY,
        "network_requirement": NetworkRequirement.REQUIRED,
        "read_write_mode": ReadWriteMode.READ_ONLY,
        "risk": RiskLevel.LOW,
        "approval_requirement": ApprovalRequirement.NONE,
        "sandbox_requirement": SandboxRequirement.REQUIRED,
        "audit_requirement": AuditRequirement.REQUIRED,
        "registered_tools": ("github.inspect",),
        "enabled": True,
        "version": "1.0",
        "schema_version": "1.0",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    base.update(overrides)
    return ConnectorSpec(**base)


def test_builtin_connectors_have_complete_declarative_contracts():
    assert len(BUILTIN_CONNECTORS) == len({item.identity for item in BUILTIN_CONNECTORS})
    for connector in BUILTIN_CONNECTORS:
        assert connector.identity and connector.description and connector.category
        assert connector.capabilities and connector.scopes
        assert connector.version and connector.schema_version == "1.0"
        assert connector.input_schema["type"] == "object"
        assert connector.output_schema["type"] == "object"
        for tool_name in connector.registered_tools:
            assert REGISTRY.get(tool_name) is not None


def test_duplicate_connectors_fail_deterministically():
    registry = ConnectorRegistry([])
    registry.register(spec())
    with pytest.raises(ConnectorRegistryError, match="already registered: custom"):
        registry.register(spec())


def test_registry_listing_is_deterministic():
    first = ConnectorRegistry([spec(identity="z"), spec(identity="a")])
    second = ConnectorRegistry([spec(identity="a"), spec(identity="z")])
    assert [item.identity for item in first.list()] == [item.identity for item in second.list()]


def test_unknown_connector_fails_closed():
    assert not ConnectorRegistry([]).authorize("unknown", [Capability.INSPECT]).allowed


def test_disabled_connector_cannot_execute():
    registry = ConnectorRegistry([spec(identity="disabled", enabled=False)])
    decision = registry.authorize("disabled", [Capability.INSPECT])
    assert not decision.allowed
    assert "disabled" in decision.reason


def test_enabled_connector_requires_registered_tool():
    with pytest.raises(ConnectorRegistryError, match="at least one registered tool"):
        ConnectorRegistry([spec(registered_tools=())])


def test_unknown_tool_reference_is_rejected():
    with pytest.raises(ConnectorRegistryError, match="unknown registered tool"):
        ConnectorRegistry([spec(registered_tools=("missing.tool",))])


def test_connector_capability_mismatch_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="capability"):
        ConnectorRegistry([spec(capabilities=(Capability.TEST.value,))])


def test_network_escalation_or_downgrade_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="network"):
        ConnectorRegistry([spec(network_requirement=NetworkRequirement.NONE)])


def test_write_escalation_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="read/write"):
        ConnectorRegistry([spec(read_write_mode=ReadWriteMode.CONTROLLED_WRITE, risk=RiskLevel.HIGH, approval_requirement=ApprovalRequirement.HUMAN_REVIEW, capabilities=(Capability.INSPECT.value, Capability.SOURCE_WRITE.value))])


def test_risk_mismatch_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="risk"):
        ConnectorRegistry([spec(risk=RiskLevel.MEDIUM)])


def test_approval_mismatch_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="approval"):
        ConnectorRegistry([spec(approval_requirement=ApprovalRequirement.EXPLICIT, risk=RiskLevel.HIGH)])


def test_sandbox_mismatch_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="sandbox"):
        ConnectorRegistry([spec(sandbox_requirement=SandboxRequirement.NONE)])


def test_audit_mismatch_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="audit"):
        ConnectorRegistry([spec(audit_requirement=AuditRequirement.NONE)])


def test_authentication_mismatch_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="authentication"):
        ConnectorRegistry([spec(authentication_method=ConnectorAuth.NONE, credential_handling=CredentialHandling.NONE)])


def test_credential_metadata_mismatch_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="credential"):
        ConnectorRegistry([spec(credential_handling=CredentialHandling.NONE)])


def test_malformed_connector_schemas_fail_closed():
    with pytest.raises(ConnectorRegistryError, match="input_schema"):
        ConnectorRegistry([spec(input_schema={"properties": {}})])
    with pytest.raises(ConnectorRegistryError, match="output_schema"):
        ConnectorRegistry([spec(output_schema={"type": "unknown"})])


def test_unknown_schema_version_fails_closed():
    with pytest.raises(ConnectorRegistryError, match="schema version"):
        ConnectorRegistry([spec(schema_version="99.0")])


def test_connector_authorization_delegates_to_existing_capability_policy():
    registry = ConnectorRegistry([spec()])
    assert registry.authorize("custom", [Capability.INSPECT]).allowed
    assert not registry.authorize("custom", []).allowed


def test_network_connector_cannot_bypass_permanent_capability_policy():
    connector = get_connector("web_research")
    assert connector is not None
    denied = ConnectorRegistry().authorize("web_research", [Capability.NETWORK], explicitly_approved=True)
    assert not denied.allowed
    assert "permanently denied" in denied.reason


def test_future_connectors_are_disabled_and_have_no_executable_tools():
    for identity in ("email", "calendar_api", "browser"):
        connector = get_connector(identity)
        assert connector is not None
        assert not connector.enabled
        assert connector.registered_tools == ()
        assert not ConnectorRegistry().authorize(identity, [Capability.NETWORK], explicitly_approved=True).allowed


def test_connector_isolation_uses_only_its_declared_tools():
    registry = ConnectorRegistry([spec()])
    assert registry.resolve_tool("custom", "github.inspect") is not None
    assert registry.resolve_tool("custom", "tests.run") is None
    assert registry.resolve_tool("missing", "github.inspect") is None


def test_custom_tool_registry_is_checked_against_connector_contract():
    custom_registry = ToolRegistry([REGISTRY.get("github.inspect")])
    registry = ConnectorRegistry([spec()], tool_registry=custom_registry)
    assert registry.authorize("custom", [Capability.INSPECT], registry=custom_registry).allowed


def test_connector_adapter_validates_request_schema_and_delegates_to_existing_boundary():
    class FakeExecutor:
        def __init__(self):
            self.calls = 0
        def execute(self, *args, **kwargs):
            self.calls += 1
            return "existing-boundary"

    adapter = ConnectorAdapter(ConnectorRegistry())
    preparation = adapter.prepare("github", "github.inspect", {"repo": "owner/repo"}, [Capability.INSPECT])
    assert preparation.authorization.allowed
    executor = FakeExecutor()
    assert adapter.execute_through_existing_boundary(preparation, executor) == "existing-boundary"
    assert executor.calls == 1


def test_adapter_rejects_malformed_input_before_execution():
    with pytest.raises(ConnectorRegistryError, match="input schema"):
        ConnectorAdapter(ConnectorRegistry()).prepare("github", "github.inspect", "not-an-object", [Capability.INSPECT])


def test_adapter_rejects_unauthorized_request_before_execution():
    class FakeExecutor:
        def execute(self, *args, **kwargs):
            raise AssertionError("must not execute")

    preparation = ConnectorAdapter(ConnectorRegistry()).prepare("github", "github.inspect", {}, [])
    assert not preparation.authorization.allowed
    with pytest.raises(ConnectorRegistryError, match="not authorized"):
        ConnectorAdapter(ConnectorRegistry()).execute_through_existing_boundary(preparation, FakeExecutor())


def test_raw_credential_values_are_rejected():
    with pytest.raises(ConnectorRegistryError, match="raw credential"):
        ConnectorAdapter(ConnectorRegistry()).prepare("github", "github.inspect", {}, [Capability.INSPECT], credential_reference="credref:password=supersecret")


def test_credential_reference_is_not_secret_material():
    preparation = ConnectorAdapter(ConnectorRegistry()).prepare(
        "github", "github.inspect", {}, [Capability.INSPECT], credential_reference="credref:github-user"
    )
    assert preparation.request.credential_reference == "credref:github-user"
    assert "secret" not in preparation.request.credential_reference
