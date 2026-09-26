"""Tests for application capability discovery, registration, input validation, and task routing."""

from __future__ import annotations

from pathlib import Path
import pytest

from autonomous_agent.application.backend import MockApplicationBackend
from autonomous_agent.application.connector import BoundedApplicationConnector
from autonomous_agent.application.models import (
    AdapterType,
    ApplicationCommand,
    ApplicationDescriptor,
    ApplicationObservation,
    ApplicationState,
)
from autonomous_agent.capability_policy import Capability
from autonomous_agent.digital.builtins import (
    BUILTIN_DECLARATIONS,
    DEFAULT_CAPABILITIES,
    ApplicationPostConditionObserver,
    build_capabilities,
)
from autonomous_agent.digital.catalog import CapabilityCatalog
from autonomous_agent.digital.contract import (
    CapabilityAvailability,
    CapabilityExecution,
    CapabilityRequest,
)
from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.digital.provider import TOOL_SANDBOX_BINDINGS
from autonomous_agent.sandbox import run_safe_operation
from autonomous_agent.tool_registry import (
    ApprovalRequirement,
    REGISTRY,
    ReadWriteMode,
    RiskLevel,
)

APPLICATION_CAPABILITY_IDS = (
    "application:list",
    "application:inspect",
    "application:observe",
    "application:command.execute",
)

APPLICATION_TOOL_NAMES = (
    "application.list",
    "application.inspect",
    "application.observe",
    "application.command.execute",
)


@pytest.fixture
def mock_application_backend() -> MockApplicationBackend:
    return MockApplicationBackend()


@pytest.fixture
def application_connector(mock_application_backend: MockApplicationBackend, tmp_path: Path) -> BoundedApplicationConnector:
    return BoundedApplicationConnector(backend=mock_application_backend, workspace_root=tmp_path)


@pytest.fixture
def application_catalog(application_connector: BoundedApplicationConnector, tmp_path: Path) -> CapabilityCatalog:
    caps = build_capabilities(
        root=tmp_path,
        connectors={"application": application_connector},
        tool_registry=REGISTRY,
    )
    return CapabilityCatalog(caps)


# =========================================================================
# 1. Registry and Spec Validation
# =========================================================================

def test_all_four_application_tools_registered_in_registry():
    for tool_name in APPLICATION_TOOL_NAMES:
        assert REGISTRY.get(tool_name) is not None, f"Tool {tool_name} not found in REGISTRY"


def test_application_tools_category_and_capability():
    for tool_name in APPLICATION_TOOL_NAMES:
        spec = REGISTRY.get(tool_name)
        assert spec.category == "application"
        assert spec.capability == Capability.APPLICATION.value


def test_application_tools_sandbox_and_audit_required():
    for tool_name in APPLICATION_TOOL_NAMES:
        spec = REGISTRY.get(tool_name)
        assert spec.sandbox_requirement.value == "required"
        assert spec.audit_requirement.value == "required"


def test_application_tools_have_sandbox_bindings():
    for tool_name in APPLICATION_TOOL_NAMES:
        assert tool_name in TOOL_SANDBOX_BINDINGS
        op, slot, key = TOOL_SANDBOX_BINDINGS[tool_name]
        assert op == "application"
        assert slot == "application"
        assert key is not None


def test_all_four_application_declarations_present():
    decl_ids = {d.capability_id for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.APPLICATION}
    assert decl_ids == set(APPLICATION_CAPABILITY_IDS)


def test_application_default_capability_is_read_only():
    defaults = DEFAULT_CAPABILITIES.get(CapabilityDomain.APPLICATION)
    assert defaults == ("application:list",)


def test_read_only_application_tools_are_safe_autonomous():
    read_only_tools = ["application.list", "application.inspect", "application.observe"]
    for tool_name in read_only_tools:
        spec = REGISTRY.get(tool_name)
        assert spec.safe_autonomous is True
        assert spec.read_write_mode is ReadWriteMode.READ_ONLY
        assert spec.approval_requirement is ApprovalRequirement.NONE
        assert spec.risk_level is RiskLevel.LOW


def test_mutating_application_command_is_controlled_write_and_approval_gated():
    spec = REGISTRY.get("application.command.execute")
    assert spec.safe_autonomous is False
    assert spec.read_write_mode is ReadWriteMode.CONTROLLED_WRITE
    assert spec.approval_requirement is ApprovalRequirement.EXPLICIT
    assert spec.risk_level is RiskLevel.HIGH


# =========================================================================
# 2. Capability Stages and Retry Policies
# =========================================================================

def test_application_stages_order():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.APPLICATION}
    assert decls["application:list"].stage == 10
    assert decls["application:inspect"].stage == 10
    assert decls["application:observe"].stage == 20
    assert decls["application:command.execute"].stage == 60


def test_read_only_application_tools_allow_retries():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.APPLICATION}
    assert decls["application:list"].retry_policy.max_attempts == 2
    assert decls["application:inspect"].retry_policy.max_attempts == 2
    assert decls["application:observe"].retry_policy.max_attempts == 2


def test_mutating_application_command_disallows_retries():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.APPLICATION}
    assert decls["application:command.execute"].retry_policy.max_attempts == 1


# =========================================================================
# 3. Discovery and Catalog Query Tests
# =========================================================================

def test_catalog_get_by_capability_id(application_catalog):
    for cap_id in APPLICATION_CAPABILITY_IDS:
        cap = application_catalog.get(cap_id)
        assert cap is not None, f"Failed to get {cap_id} from catalog"
        assert cap.descriptor.capability_id == cap_id


def test_catalog_by_tool_name(application_catalog):
    for tool_name in APPLICATION_TOOL_NAMES:
        cap = application_catalog.by_tool(tool_name)
        assert cap is not None, f"Failed to get {tool_name} from catalog"
        assert cap.descriptor.tool_name == tool_name


def test_catalog_discover_domain_application(application_catalog):
    caps = application_catalog.discover(domain=CapabilityDomain.APPLICATION)
    assert len(caps) == 4
    ids = {c.capability_id for c in caps}
    assert ids == set(APPLICATION_CAPABILITY_IDS)


def test_catalog_discover_by_query_observe(application_catalog):
    results = application_catalog.discover("observe", domain=CapabilityDomain.APPLICATION)
    found_ids = {r.capability_id for r in results}
    assert "application:observe" in found_ids


def test_domain_status_with_mock_connector_reports_usable(application_catalog):
    status = {item["domain"]: item for item in application_catalog.domain_status()}
    assert status["application"]["usable"] is True
    assert status["application"]["registered_capabilities"] == 4


def test_catalog_documentation_includes_application_domain(application_catalog):
    docs = application_catalog.documentation()
    domain_names = {d.domain for d in docs.domains}
    assert "application" in domain_names
    cap_ids = {c.capability_id for c in docs.capabilities}
    for cid in APPLICATION_CAPABILITY_IDS:
        assert cid in cap_ids


# =========================================================================
# 4. Natural Language Routing Tests
# =========================================================================

def test_route_list_applications(application_catalog):
    res = application_catalog.route("list applications available")
    assert "application:list" in res.selected
    assert CapabilityDomain.APPLICATION in res.matched_domains


def test_route_inspect_application(application_catalog):
    res = application_catalog.route("inspect application details")
    assert "application:inspect" in res.selected


def test_route_observe_application(application_catalog):
    res = application_catalog.route("inside the app check status")
    assert "application:observe" in res.selected


def test_route_execute_command(application_catalog):
    res = application_catalog.route("run command in application editor")
    assert "application:command.execute" in res.selected


# =========================================================================
# 5. Input Validation across all 4 capabilities
# =========================================================================

def test_validate_input_list_valid(application_catalog):
    cap = application_catalog.get("application:list")
    assert cap.validate_input({}).ok


def test_validate_input_list_extra_rejected(application_catalog):
    cap = application_catalog.get("application:list")
    assert not cap.validate_input({"extra": "not_allowed"}).ok


def test_validate_input_inspect_valid(application_catalog):
    cap = application_catalog.get("application:inspect")
    assert cap.validate_input({"app_id": "vscode"}).ok


def test_validate_input_inspect_missing_app_id(application_catalog):
    cap = application_catalog.get("application:inspect")
    assert not cap.validate_input({}).ok


def test_validate_input_observe_valid(application_catalog):
    cap = application_catalog.get("application:observe")
    assert cap.validate_input({}).ok
    assert cap.validate_input({"app_id": "vscode"}).ok


def test_validate_input_observe_extra_rejected(application_catalog):
    cap = application_catalog.get("application:observe")
    assert not cap.validate_input({"app_id": "vscode", "extra": 123}).ok


def test_validate_input_command_execute_valid(application_catalog):
    cap = application_catalog.get("application:command.execute")
    assert cap.validate_input({"command": "read_text", "app_id": "vscode", "args": ["main.py"]}).ok


def test_validate_input_command_execute_missing_command(application_catalog):
    cap = application_catalog.get("application:command.execute")
    assert not cap.validate_input({"app_id": "vscode"}).ok


def test_validate_input_command_execute_missing_app_id(application_catalog):
    cap = application_catalog.get("application:command.execute")
    assert not cap.validate_input({"command": "read_text"}).ok


# =========================================================================
# 6. End-to-End Sandbox Dispatch and Post-Condition Observer Tests
# =========================================================================

def test_sandbox_dispatches_list_applications(application_connector, tmp_path):
    result = run_safe_operation(
        "application",
        tmp_path,
        application_connector=application_connector,
        application_request={"operation": "list_applications"},
    )
    assert result.success is True
    assert "vscode" in result.output


def test_sandbox_dispatches_get_application(application_connector, tmp_path):
    result = run_safe_operation(
        "application",
        tmp_path,
        application_connector=application_connector,
        application_request={"operation": "get_application", "app_id": "vscode"},
    )
    assert result.success is True
    assert "vscode" in result.output


def test_application_capability_execution_and_observation(application_catalog, application_connector):
    cap = application_catalog.get("application:list")
    req = CapabilityRequest("application:list", "application.list", {})
    outcome = cap.bounded_retry(req)
    assert outcome.state == "verified"
    assert outcome.execution.success is True
    assert outcome.observation.observed is True


def test_application_command_execute_requires_explicit_approval(application_catalog):
    cap = application_catalog.get("application:command.execute")
    denied = cap.authorize(granted=[Capability.APPLICATION], explicitly_approved=False)
    assert denied.allowed is False
    allowed = cap.authorize(granted=[Capability.APPLICATION], explicitly_approved=True)
    assert allowed.allowed is True


def test_application_read_only_tools_autonomous(application_catalog):
    for cap_id in ("application:list", "application:inspect", "application:observe"):
        cap = application_catalog.get(cap_id)
        decision = cap.authorize(granted=[Capability.APPLICATION], explicitly_approved=False)
        assert decision.allowed is True, cap_id


def test_application_observer_handles_execution_failure():
    observer = ApplicationPostConditionObserver(connector=None)
    req = CapabilityRequest("application:list", "application.list", {})
    exec_fail = CapabilityExecution("application:list", False, error="Backend failed", boundary="application")
    obs = observer.observe(req, exec_fail)
    assert obs.observed is False
    assert "execution failed" in obs.detail


def test_application_observer_command_verifies_via_observation(application_connector):
    application_connector.open_session("vscode")
    observer = ApplicationPostConditionObserver(application_connector)
    req = CapabilityRequest("application:command.execute", "application.command.execute", {"command": "read_text", "app_id": "vscode"}, approved=True)
    exec_success = CapabilityExecution(
        "application:command.execute",
        True,
        evidence={"app_id": "vscode", "status_message": "read_text executed successfully"},
        boundary="application",
    )
    obs = observer.observe(req, exec_success)
    assert obs.observed is True
    assert "verified" in obs.detail
