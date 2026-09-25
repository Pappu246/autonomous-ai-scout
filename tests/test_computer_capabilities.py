"""Tests for computer capability discovery, registration, input validation, and task routing."""

from __future__ import annotations

import pytest

from autonomous_agent.computer.backend import MockComputerBackend
from autonomous_agent.computer.connector import BoundedComputerConnector
from autonomous_agent.digital.builtins import (
    BUILTIN_DECLARATIONS,
    DEFAULT_CAPABILITIES,
    build_capabilities,
)
from autonomous_agent.digital.catalog import CapabilityCatalog
from autonomous_agent.digital.contract import (
    CapabilityAvailability,
    CapabilityDescriptor,
    InputValidation,
)
from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.digital.provider import TOOL_SANDBOX_BINDINGS
from autonomous_agent.tool_registry import REGISTRY, ReadWriteMode, RiskLevel
from tests.digital_support import RecordingExecutor, build_catalog


COMPUTER_CAPABILITY_IDS = (
    "computer:screen.capture",
    "computer:window.list",
    "computer:window.active",
    "computer:window.focus",
    "computer:app.launch",
    "computer:mouse.move",
    "computer:mouse.click",
    "computer:keyboard.type",
    "computer:keyboard.hotkey",
    "computer:clipboard.read",
    "computer:clipboard.write",
)

COMPUTER_TOOL_NAMES = (
    "computer.screen.capture",
    "computer.window.list",
    "computer.window.active",
    "computer.window.focus",
    "computer.app.launch",
    "computer.mouse.move",
    "computer.mouse.click",
    "computer.keyboard.type",
    "computer.keyboard.hotkey",
    "computer.clipboard.read",
    "computer.clipboard.write",
)


@pytest.fixture
def computer_catalog() -> CapabilityCatalog:
    connector = BoundedComputerConnector(backend=MockComputerBackend())
    caps = build_capabilities(
        root=".",
        connectors={"computer": connector},
        tool_registry=REGISTRY,
    )
    return CapabilityCatalog(caps)


# =========================================================================
# 1. Registry and Spec Validation (6 tests)
# =========================================================================

def test_all_eleven_computer_tools_registered_in_registry():
    for tool_name in COMPUTER_TOOL_NAMES:
        assert REGISTRY.get(tool_name) is not None, f"Tool {tool_name} not found in REGISTRY"


def test_computer_tools_category():
    for tool_name in COMPUTER_TOOL_NAMES:
        spec = REGISTRY.get(tool_name)
        assert spec.category == "computer"


def test_computer_tools_sandbox_and_audit_required():
    for tool_name in COMPUTER_TOOL_NAMES:
        spec = REGISTRY.get(tool_name)
        assert spec.sandbox_requirement.value == "required"
        assert spec.audit_requirement.value == "required"


def test_computer_tools_have_sandbox_bindings():
    for tool_name in COMPUTER_TOOL_NAMES:
        assert tool_name in TOOL_SANDBOX_BINDINGS
        op, slot, key = TOOL_SANDBOX_BINDINGS[tool_name]
        assert op == "computer"
        assert slot == "computer"
        assert key is not None


def test_all_eleven_computer_declarations_present():
    decl_ids = {d.capability_id for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.COMPUTER}
    assert decl_ids == set(COMPUTER_CAPABILITY_IDS)


def test_computer_default_capability_is_read_only():
    defaults = DEFAULT_CAPABILITIES.get(CapabilityDomain.COMPUTER)
    assert defaults == ("computer:window.active",)


# =========================================================================
# 2. Capability Stages and Retry Policies (3 tests)
# =========================================================================

def test_computer_stages_order():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.COMPUTER}
    # Read/inspect tools are earlier stages (10..30)
    assert decls["computer:screen.capture"].stage == 10
    assert decls["computer:window.list"].stage == 10
    assert decls["computer:window.active"].stage == 10
    assert decls["computer:clipboard.read"].stage == 10
    assert decls["computer:mouse.move"].stage == 20
    assert decls["computer:window.focus"].stage == 30
    # Mutating / action tools are later stages (40..60)
    assert decls["computer:mouse.click"].stage == 40
    assert decls["computer:keyboard.hotkey"].stage == 40
    assert decls["computer:keyboard.type"].stage == 50
    assert decls["computer:clipboard.write"].stage == 50
    assert decls["computer:app.launch"].stage == 60


def test_read_only_computer_tools_allow_retries():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.COMPUTER}
    assert decls["computer:screen.capture"].retry_policy.max_attempts == 2
    assert decls["computer:window.list"].retry_policy.max_attempts == 2
    assert decls["computer:window.active"].retry_policy.max_attempts == 2
    assert decls["computer:clipboard.read"].retry_policy.max_attempts == 2


def test_mutating_computer_tools_disallow_retries():
    decls = {d.capability_id: d for d in BUILTIN_DECLARATIONS if d.domain is CapabilityDomain.COMPUTER}
    assert decls["computer:app.launch"].retry_policy.max_attempts == 1
    assert decls["computer:mouse.click"].retry_policy.max_attempts == 1
    assert decls["computer:keyboard.type"].retry_policy.max_attempts == 1
    assert decls["computer:keyboard.hotkey"].retry_policy.max_attempts == 1
    assert decls["computer:clipboard.write"].retry_policy.max_attempts == 1


# =========================================================================
# 3. Discovery and Catalog Query Tests (8 tests)
# =========================================================================

def test_catalog_get_by_capability_id(computer_catalog):
    for cap_id in COMPUTER_CAPABILITY_IDS:
        cap = computer_catalog.get(cap_id)
        assert cap is not None, f"Failed to get {cap_id} from catalog"
        assert cap.descriptor.capability_id == cap_id


def test_catalog_by_tool_name(computer_catalog):
    for tool_name in COMPUTER_TOOL_NAMES:
        cap = computer_catalog.by_tool(tool_name)
        assert cap is not None, f"Failed to get {tool_name} from catalog"
        assert cap.descriptor.tool_name == tool_name


def test_catalog_discover_domain_computer(computer_catalog):
    caps = computer_catalog.discover(domain=CapabilityDomain.COMPUTER)
    assert len(caps) == 11
    ids = {c.capability_id for c in caps}
    assert ids == set(COMPUTER_CAPABILITY_IDS)


def test_catalog_discover_by_query_window(computer_catalog):
    results = computer_catalog.discover("window", domain=CapabilityDomain.COMPUTER)
    found_ids = {r.capability_id for r in results}
    assert "computer:window.list" in found_ids
    assert "computer:window.active" in found_ids
    assert "computer:window.focus" in found_ids


def test_catalog_discover_by_query_clipboard(computer_catalog):
    results = computer_catalog.discover("clipboard", domain=CapabilityDomain.COMPUTER)
    found_ids = {r.capability_id for r in results}
    assert "computer:clipboard.read" in found_ids
    assert "computer:clipboard.write" in found_ids


def test_domain_status_with_mock_connector_reports_usable(computer_catalog):
    status = {item["domain"]: item for item in computer_catalog.domain_status()}
    assert status["computer"]["usable"] is True
    assert status["computer"]["registered_capabilities"] == 11


def test_catalog_documentation_includes_computer_domain(computer_catalog):
    docs = computer_catalog.documentation()
    domain_names = {d.domain for d in docs.domains}
    assert "computer" in domain_names
    cap_ids = {c.capability_id for c in docs.capabilities}
    for cid in COMPUTER_CAPABILITY_IDS:
        assert cid in cap_ids


def test_catalog_documentation_json_serialization(computer_catalog):
    docs = computer_catalog.documentation()
    raw_json = docs.to_json()
    assert "computer:screen.capture" in raw_json
    assert "computer:app.launch" in raw_json


# =========================================================================
# 4. Natural Language Routing Tests (14 tests)
# =========================================================================

def test_route_list_windows(computer_catalog):
    """Bug G regression: natural language 'list windows' must route to computer:window.list."""
    res = computer_catalog.route("list windows")
    assert "computer:window.list" in res.selected
    assert CapabilityDomain.COMPUTER in res.matched_domains


def test_route_list_window_singular(computer_catalog):
    res = computer_catalog.route("list window")
    assert "computer:window.list" in res.selected


def test_route_active_window(computer_catalog):
    res = computer_catalog.route("which window is active right now")
    assert "computer:window.active" in res.selected


def test_route_focus_window(computer_catalog):
    res = computer_catalog.route("focus window Notepad")
    assert "computer:window.focus" in res.selected


def test_route_app_launch(computer_catalog):
    res = computer_catalog.route("launch application calculator")
    assert "computer:app.launch" in res.selected


def test_route_open_application(computer_catalog):
    res = computer_catalog.route("open an application called paint")
    assert "computer:app.launch" in res.selected


def test_route_screenshot(computer_catalog):
    res = computer_catalog.route("take a screenshot of the screen")
    assert "computer:screen.capture" in res.selected


def test_route_capture_screen(computer_catalog):
    res = computer_catalog.route("capture screen content")
    assert "computer:screen.capture" in res.selected


def test_route_mouse_move(computer_catalog):
    res = computer_catalog.route("move mouse to location")
    assert "computer:mouse.move" in res.selected


def test_route_mouse_click(computer_catalog):
    res = computer_catalog.route("click mouse on the button")
    assert "computer:mouse.click" in res.selected


def test_route_type_text(computer_catalog):
    res = computer_catalog.route("type text hello in the window")
    assert "computer:keyboard.type" in res.selected


def test_route_hotkey(computer_catalog):
    res = computer_catalog.route("press hotkey ctrl+c")
    assert "computer:keyboard.hotkey" in res.selected


def test_route_read_clipboard(computer_catalog):
    res = computer_catalog.route("read clipboard contents")
    assert "computer:clipboard.read" in res.selected


def test_route_write_clipboard(computer_catalog):
    res = computer_catalog.route("write clipboard with updated notes")
    assert "computer:clipboard.write" in res.selected


# =========================================================================
# 5. Input Validation across all 11 capabilities (21 tests)
# =========================================================================

def test_validate_input_screen_capture_valid(computer_catalog):
    cap = computer_catalog.get("computer:screen.capture")
    val = cap.validate_input({"region": {"x": 10, "y": 20, "width": 300, "height": 200}})
    assert val.ok


def test_validate_input_screen_capture_extra_field_rejected(computer_catalog):
    cap = computer_catalog.get("computer:screen.capture")
    val = cap.validate_input({"extra": "not_allowed"})
    assert not val.ok


def test_validate_input_window_list_valid(computer_catalog):
    cap = computer_catalog.get("computer:window.list")
    assert cap.validate_input({"filter": "Notepad"}).ok
    assert cap.validate_input({}).ok


def test_validate_input_window_list_extra_rejected(computer_catalog):
    cap = computer_catalog.get("computer:window.list")
    assert not cap.validate_input({"unexpected": 123}).ok


def test_validate_input_window_active_valid(computer_catalog):
    cap = computer_catalog.get("computer:window.active")
    assert cap.validate_input({}).ok


def test_validate_input_window_active_extra_rejected(computer_catalog):
    cap = computer_catalog.get("computer:window.active")
    assert not cap.validate_input({"unknown": True}).ok


def test_validate_input_window_focus_valid(computer_catalog):
    cap = computer_catalog.get("computer:window.focus")
    assert cap.validate_input({"handle": 1234}).ok
    assert cap.validate_input({"title": "Calculator"}).ok


def test_validate_input_window_focus_extra_rejected(computer_catalog):
    cap = computer_catalog.get("computer:window.focus")
    assert not cap.validate_input({"color": "red"}).ok


def test_validate_input_app_launch_valid(computer_catalog):
    cap = computer_catalog.get("computer:app.launch")
    assert cap.validate_input({"app": "calc.exe", "argv": ["--arg1"]}).ok


def test_validate_input_app_launch_missing_required_app(computer_catalog):
    cap = computer_catalog.get("computer:app.launch")
    assert not cap.validate_input({"argv": ["--test"]}).ok


def test_validate_input_app_launch_extra_rejected(computer_catalog):
    cap = computer_catalog.get("computer:app.launch")
    assert not cap.validate_input({"app": "calc.exe", "shell": True}).ok


def test_validate_input_mouse_move_valid(computer_catalog):
    cap = computer_catalog.get("computer:mouse.move")
    assert cap.validate_input({"x": 100, "y": 200}).ok


def test_validate_input_mouse_move_missing_coords(computer_catalog):
    cap = computer_catalog.get("computer:mouse.move")
    assert not cap.validate_input({"x": 100}).ok
    assert not cap.validate_input({"y": 200}).ok


def test_validate_input_mouse_click_valid(computer_catalog):
    cap = computer_catalog.get("computer:mouse.click")
    assert cap.validate_input({"x": 50, "y": 50, "button": "left", "clicks": 1}).ok
    assert cap.validate_input({}).ok


def test_validate_input_keyboard_type_valid(computer_catalog):
    cap = computer_catalog.get("computer:keyboard.type")
    assert cap.validate_input({"text": "hello"}).ok


def test_validate_input_keyboard_type_missing_text(computer_catalog):
    cap = computer_catalog.get("computer:keyboard.type")
    assert not cap.validate_input({}).ok


def test_validate_input_keyboard_hotkey_valid(computer_catalog):
    cap = computer_catalog.get("computer:keyboard.hotkey")
    assert cap.validate_input({"keys": ["ctrl", "c"]}).ok
    assert cap.validate_input({"hotkey": "ctrl+c"}).ok


def test_validate_input_clipboard_read_valid(computer_catalog):
    cap = computer_catalog.get("computer:clipboard.read")
    assert cap.validate_input({}).ok


def test_validate_input_clipboard_read_extra_rejected(computer_catalog):
    cap = computer_catalog.get("computer:clipboard.read")
    assert not cap.validate_input({"extra": 1}).ok


def test_validate_input_clipboard_write_valid(computer_catalog):
    cap = computer_catalog.get("computer:clipboard.write")
    assert cap.validate_input({"text": "sample text"}).ok


def test_validate_input_clipboard_write_missing_text(computer_catalog):
    cap = computer_catalog.get("computer:clipboard.write")
    assert not cap.validate_input({}).ok
