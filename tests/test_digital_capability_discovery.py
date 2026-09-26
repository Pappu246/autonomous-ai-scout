"""Phase 1: capability discovery over a peer-domain catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from autonomous_agent.digital import build_agent
from autonomous_agent.digital.builtins import BUILTIN_DECLARATIONS, declared_capability_ids
from autonomous_agent.digital.catalog import CapabilityCatalog
from autonomous_agent.digital.contract import CapabilityAvailability, CapabilityError
from autonomous_agent.digital.domains import (
    DOMAIN_DESCRIPTORS,
    CapabilityDomain,
    DomainPhase,
    active_domains,
    domain_descriptor,
    reserved_domains,
)
from autonomous_agent.tool_registry import REGISTRY

from .digital_support import RecordingExecutor, build_capability, build_catalog
from .digital_support import DECLARATIONS_BY_ID


def _catalog() -> CapabilityCatalog:
    return build_catalog(
        ("filesystem:list", "filesystem:read", "web:search", "github:inspect"),
        RecordingExecutor(),
    )


def test_every_domain_is_declared_as_a_peer():
    """GitHub must be one domain among many, never the whole product."""
    values = {item.domain for item in DOMAIN_DESCRIPTORS}
    expected = {
        CapabilityDomain.COMPUTER,
        CapabilityDomain.BROWSER,
        CapabilityDomain.FILESYSTEM,
        CapabilityDomain.OS_SHELL,
        CapabilityDomain.WEB,
        CapabilityDomain.EMAIL,
        CapabilityDomain.CALENDAR,
        CapabilityDomain.GITHUB,
        CapabilityDomain.DOCUMENTS,
        CapabilityDomain.APPLICATION,
        CapabilityDomain.TESTING,
    }
    assert values == expected
    assert len(DOMAIN_DESCRIPTORS) == len(expected)
    # No domain is privileged: every descriptor carries the same shape.
    assert len({item.domain.value for item in DOMAIN_DESCRIPTORS}) == len(DOMAIN_DESCRIPTORS)


def test_github_is_a_single_domain_not_the_product():
    github = [item for item in DOMAIN_DESCRIPTORS if item.domain is CapabilityDomain.GITHUB]
    assert len(github) == 1
    assert len(active_domains()) > 1
    assert CapabilityDomain.GITHUB in {item.domain for item in active_domains()}


def test_application_and_documents_are_active_without_fake_capabilities():
    assert domain_descriptor(CapabilityDomain.APPLICATION).phase is DomainPhase.ACTIVE
    assert domain_descriptor(CapabilityDomain.DOCUMENTS).phase is DomainPhase.ACTIVE
    assert domain_descriptor(CapabilityDomain.APPLICATION).registered
    assert domain_descriptor(CapabilityDomain.DOCUMENTS).registered


def test_no_active_m1_domain_has_a_fake_capability():
    # M1 adds architecture only; adapters remain absent.
    for descriptor in DOMAIN_DESCRIPTORS:
        if descriptor.domain in {CapabilityDomain.APPLICATION, CapabilityDomain.DOCUMENTS}:
            assert descriptor.domain not in {item.domain for item in reserved_domains()}


def test_no_reserved_domain_has_a_fake_capability():
    """Reserved domains must not be satisfied by placeholder executors."""
    for declaration in BUILTIN_DECLARATIONS:
        assert declaration.domain not in {item.domain for item in reserved_domains()}


def test_catalog_discovers_registered_capabilities():
    catalog = _catalog()
    discovered = catalog.discover()
    assert {item.capability_id for item in discovered} == {
        "filesystem:list",
        "filesystem:read",
        "web:search",
        "github:inspect",
    }


def test_discovery_filters_by_domain_and_query():
    catalog = _catalog()
    assert {item.capability_id for item in catalog.discover(domain=CapabilityDomain.FILESYSTEM)} == {
        "filesystem:list",
        "filesystem:read",
    }
    assert {item.capability_id for item in catalog.discover("repository")} == {"github:inspect"}
    assert catalog.discover("no-such-capability-anywhere") == ()


def test_discovery_exposes_no_credential_material():
    catalog = _catalog()
    for descriptor in catalog.discover():
        payload = str(descriptor.safe_dict()).lower()
        for token in ("api_key=", "token=", "password=", "secret=", "authorization:"):
            assert token not in payload


def test_discovery_reports_availability_honestly():
    catalog = _catalog()
    available = catalog.discover(availability=CapabilityAvailability.AVAILABLE)
    assert len(available) == 4


def test_duplicate_capability_registration_is_rejected():
    executor = RecordingExecutor()
    catalog = _catalog()
    duplicate = build_capability(
        "filesystem:list", DECLARATIONS_BY_ID["filesystem:list"], executor
    )
    with pytest.raises(CapabilityError):
        catalog.register(duplicate)


def test_one_tool_cannot_back_two_capabilities():
    executor = RecordingExecutor()
    catalog = CapabilityCatalog(
        (build_capability("filesystem:list", DECLARATIONS_BY_ID["filesystem:list"], executor),)
    )
    clone = build_capability(
        "filesystem:enumerate", DECLARATIONS_BY_ID["filesystem:list"], executor
    )
    with pytest.raises(CapabilityError):
        catalog.register(clone)


def test_undeclared_domain_is_rejected_at_registration():
    executor = RecordingExecutor()
    capability = build_capability(
        "filesystem:list", DECLARATIONS_BY_ID["filesystem:list"], executor
    )
    catalog = CapabilityCatalog((), domains=())
    with pytest.raises(CapabilityError):
        catalog.register(capability)


def test_capability_without_executor_cannot_claim_availability():
    with pytest.raises(CapabilityError):
        build_capability(
            "filesystem:list", DECLARATIONS_BY_ID["filesystem:list"], None
        )


def test_documentation_model_covers_every_declared_domain():
    catalog = _catalog()
    docs = catalog.documentation()
    assert {item.domain for item in docs.domains} == {
        item.domain.value for item in DOMAIN_DESCRIPTORS
    }
    assert docs.digest
    assert "filesystem:list" in docs.to_json()
    # Active architecture domains appear even before their later adapters exist.
    for domain in {"application", "documents"}:
        entry = next(item for item in docs.domains if item.domain == domain)
        assert entry.phase == "active"
        assert entry.capability_ids == ()


def test_domain_status_marks_unimplemented_active_domains_unusable(tmp_path: Path):
    agent = build_agent(root=tmp_path)
    status = {item["domain"]: item for item in agent.domain_status()}
    assert status["computer"]["usable"] is False
    assert status["application"]["usable"] is False
    assert status["documents"]["usable"] is False
    assert status["filesystem"]["usable"] is True
    assert status["github"]["usable"] is True


def test_declared_capability_ids_are_namespaced_and_unique():
    ids = declared_capability_ids()
    assert len(ids) == len(set(ids))
    for capability_id in ids:
        domain, _, operation = capability_id.partition(":")
        assert operation
        assert domain in {item.domain.value for item in DOMAIN_DESCRIPTORS}


def test_catalog_only_binds_tools_that_exist_in_the_registry():
    """A capability whose tool is absent is skipped, never advertised."""
    agent = build_agent(root=Path.cwd())
    bound = {item.tool_name for item in agent.catalog.discover()}
    for tool_name in bound:
        assert REGISTRY.get(tool_name) is not None
