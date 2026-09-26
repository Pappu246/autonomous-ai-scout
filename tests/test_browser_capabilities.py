"""Capability registration, routing and verification tests (Phase 3, M3).

Verifies that the 12 canonical browser capabilities are registered in the
central ToolRegistry, bound through the sandbox, built into the digital
capability catalog, and verified through the browser post-condition observer --
and that legacy browser.open/click/extract compatibility is preserved while
application and documents stay RESERVED.
"""

from __future__ import annotations

import pytest

from autonomous_agent.browser import BoundedBrowserConnector, MockBrowserBackend, PageElement
from autonomous_agent.browser.backend import _MockPage
from autonomous_agent.capability_policy import Capability
from autonomous_agent.digital.builtins import build_capabilities
from autonomous_agent.digital.contract import CapabilityRequest
from autonomous_agent.digital.domains import CapabilityDomain, domain_descriptor
from autonomous_agent.sandbox import run_safe_operation
from autonomous_agent.tool_registry import (
    ApprovalRequirement,
    REGISTRY,
    ReadWriteMode,
    RiskLevel,
)

NEW_CAPABILITIES = [
    ("browser.session.open", "session_open", True),
    ("browser.navigate", "navigate", True),
    ("browser.back", "back", True),
    ("browser.forward", "forward", True),
    ("browser.reload", "reload", True),
    ("browser.page.observe", "page_observe", True),
    ("browser.element.find", "element_find", True),
    ("browser.element.click", "element_click", False),
    ("browser.element.type", "element_type", False),
    ("browser.element.select", "element_select", False),
    ("browser.download.start", "download_start", False),
    ("browser.file.extract", "file_extract", True),
]

LEGACY_CAPABILITIES = ["browser.open", "browser.click", "browser.extract"]


def cid(tool_name: str) -> str:
    """Map a tool name (dots) to its capability id (domain:operation)."""
    return tool_name.replace(".", ":", 1)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
def test_twelve_browser_tools_are_registered():
    for tool_name, _op, _safe in NEW_CAPABILITIES:
        tool = REGISTRY.get(tool_name)
        assert tool is not None, tool_name
        assert tool.capability == Capability.BROWSER.value


def test_read_only_browser_tools_are_safe_autonomous():
    for tool_name, _op, safe in NEW_CAPABILITIES:
        tool = REGISTRY.get(tool_name)
        assert tool.safe_autonomous is safe
        if safe:
            assert tool.read_write_mode is ReadWriteMode.READ_ONLY
            assert tool.approval_requirement is ApprovalRequirement.NONE
        else:
            assert tool.read_write_mode is ReadWriteMode.CONTROLLED_WRITE
            assert tool.approval_requirement is ApprovalRequirement.EXPLICIT
            assert tool.risk_level is RiskLevel.HIGH


def test_legacy_browser_tools_preserved():
    for tool_name in LEGACY_CAPABILITIES:
        tool = REGISTRY.get(tool_name)
        assert tool is not None
        assert tool.capability == Capability.BROWSER.value
        assert tool.safe_autonomous is True
        assert tool.read_write_mode is ReadWriteMode.READ_ONLY


# --------------------------------------------------------------------------
# Sandbox bindings
# --------------------------------------------------------------------------
def test_sandbox_dispatches_advanced_browser_ops(tmp_path):
    from autonomous_agent.digital.provider import TOOL_SANDBOX_BINDINGS

    for tool_name, op, _safe in NEW_CAPABILITIES:
        assert TOOL_SANDBOX_BINDINGS[tool_name] == ("browser", "browser", op)
    for tool_name in LEGACY_CAPABILITIES:
        assert TOOL_SANDBOX_BINDINGS[tool_name][0] == "browser"


def test_sandbox_rejects_unknown_browser_operation(tmp_path):
    connector = _make_connector(tmp_path)
    result = run_safe_operation(
        "browser", tmp_path, browser_connector=connector, browser_request={"operation": "eval_js"}
    )
    assert result.success is False


# --------------------------------------------------------------------------
# Capability build + end-to-end verification through the observer
# --------------------------------------------------------------------------
def _make_connector(tmp_path) -> BoundedBrowserConnector:
    backend = MockBrowserBackend()
    backend.add_page(
        _MockPage(
            url="https://example.com/",
            title="Home",
            text="Welcome",
            elements=(
                PageElement(element_id="e-q", role="textbox", accessible_name="Query",
                            tag="input", selector="#q", input_type="text"),
                PageElement(element_id="e-size", role="combobox", accessible_name="Size",
                            tag="select", selector="#size", options=("S", "M", "L")),
            ),
        )
    )
    backend.add_page(
        _MockPage(
            url="https://shop.example.com/",
            title="Shop",
            text="Shop",
            downloads={"https://shop.example.com/f.pdf": (b"DATA-123", "application/pdf")},
        )
    )
    return BoundedBrowserConnector(
        backend=backend, allowed_hosts=["example.com", "shop.example.com"], workspace_root=tmp_path
    )


def _capabilities(tmp_path, connector):
    caps = build_capabilities(root=tmp_path, connectors={"browser": connector})
    return {c.descriptor.capability_id: c for c in caps}


def test_browser_capabilities_are_built_with_observer(tmp_path):
    connector = _make_connector(tmp_path)
    caps = _capabilities(tmp_path, connector)
    for tool_name, _op, _safe in NEW_CAPABILITIES:
        assert cid(tool_name) in caps, tool_name
    assert caps[cid("browser.navigate")]._observer is not None


def test_navigate_and_observe_verify_end_to_end(tmp_path):
    connector = _make_connector(tmp_path)
    caps = _capabilities(tmp_path, connector)
    open_out = caps["browser:session.open"].bounded_retry(
        CapabilityRequest("browser:session.open", "browser.session.open",
                          {"allowed_hosts": ["example.com", "shop.example.com"]})
    )
    assert open_out.state == "verified"
    nav = caps["browser:navigate"].bounded_retry(
        CapabilityRequest("browser:navigate", "browser.navigate", {"url": "https://example.com/"})
    )
    assert nav.state == "verified"
    obs = caps["browser:page.observe"].bounded_retry(
        CapabilityRequest("browser:page.observe", "browser.page.observe", {})
    )
    assert obs.state == "verified"


def test_element_type_verifies_via_reobservation(tmp_path):
    connector = _make_connector(tmp_path)
    caps = _capabilities(tmp_path, connector)
    caps["browser:session.open"].bounded_retry(
        CapabilityRequest("browser:session.open", "browser.session.open",
                          {"allowed_hosts": ["example.com", "shop.example.com"]})
    )
    caps["browser:navigate"].bounded_retry(
        CapabilityRequest("browser:navigate", "browser.navigate", {"url": "https://example.com/"})
    )
    out = caps["browser:element.type"].bounded_retry(
        CapabilityRequest("browser:element.type", "browser.element.type",
                          {"text": "laptops", "selector": "#q"}, approved=True)
    )
    assert out.state == "verified"


def test_download_verifies_via_checksum_reread(tmp_path):
    connector = _make_connector(tmp_path)
    caps = _capabilities(tmp_path, connector)
    caps["browser:session.open"].bounded_retry(
        CapabilityRequest("browser:session.open", "browser.session.open",
                          {"allowed_hosts": ["example.com", "shop.example.com"]})
    )
    caps["browser:navigate"].bounded_retry(
        CapabilityRequest("browser:navigate", "browser.navigate", {"url": "https://shop.example.com/"})
    )
    out = caps[cid("browser.download.start")].bounded_retry(
        CapabilityRequest(cid("browser.download.start"), "browser.download.start",
                          {"url": "https://shop.example.com/f.pdf"}, approved=True)
    )
    assert out.state == "verified"


# --------------------------------------------------------------------------
# Approval boundaries
# --------------------------------------------------------------------------
def test_mutating_capabilities_require_approval(tmp_path):
    connector = _make_connector(tmp_path)
    caps = _capabilities(tmp_path, connector)
    for tool_name in ("browser.element.click", "browser.element.type",
                      "browser.element.select", "browser.download.start"):
        cap = caps[cid(tool_name)]
        denied = cap.authorize(granted=[Capability.BROWSER], explicitly_approved=False)
        assert denied.allowed is False, tool_name
        allowed = cap.authorize(granted=[Capability.BROWSER], explicitly_approved=True)
        assert allowed.allowed is True, tool_name


def test_read_only_capabilities_are_autonomous(tmp_path):
    connector = _make_connector(tmp_path)
    caps = _capabilities(tmp_path, connector)
    for tool_name in ("browser:navigate", "browser.page.observe", "browser.element.find"):
        decision = caps[cid(tool_name)].authorize(granted=[Capability.BROWSER], explicitly_approved=False)
        assert decision.allowed is True, tool_name


# --------------------------------------------------------------------------
# Active architecture domains without M1 execution backends
# --------------------------------------------------------------------------
def test_application_and_documents_are_active_architecture_domains():
    assert domain_descriptor(CapabilityDomain.APPLICATION).registered is True
    assert domain_descriptor(CapabilityDomain.DOCUMENTS).registered is True
    assert domain_descriptor(CapabilityDomain.BROWSER).registered is True
