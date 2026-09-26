"""Phase 4 M1 architecture and fail-closed policy contracts."""

from pathlib import Path

import pytest

from autonomous_agent.application import (
    ApplicationManifest,
    ApplicationPolicyError,
    ApplicationRequest,
    InvocationKind,
)
from autonomous_agent.capability_policy import Capability, check_capability
from autonomous_agent.digital.domains import CapabilityDomain, DomainPhase, domain_descriptor, reserved_domains
from autonomous_agent.documents import (
    DocumentFormat,
    DocumentInput,
    DocumentPolicyError,
    DocumentReference,
    ExtractedContent,
)


def test_m1_capability_ids_are_stable_and_safe():
    assert Capability.APPLICATION.value == "application"
    assert Capability.DOCUMENTS.value == "documents"
    for capability in (Capability.APPLICATION, Capability.DOCUMENTS):
        assert capability in __import__("autonomous_agent.capability_policy", fromlist=["SAFE_CAPABILITIES"]).SAFE_CAPABILITIES
        assert check_capability(capability, [capability]).allowed


def test_only_application_and_documents_change_to_active():
    assert domain_descriptor(CapabilityDomain.APPLICATION).phase is DomainPhase.ACTIVE
    assert domain_descriptor(CapabilityDomain.DOCUMENTS).phase is DomainPhase.ACTIVE
    assert {item.domain for item in reserved_domains()} == set()


def test_document_policy_confines_workspace_and_rejects_traversal(tmp_path: Path):
    document = DocumentInput(DocumentReference("reports/report.pdf"), DocumentFormat.PDF, 10)
    from autonomous_agent.documents import DEFAULT_DOCUMENT_POLICY
    assert DEFAULT_DOCUMENT_POLICY.validate(document, tmp_path) == (tmp_path / "reports/report.pdf").resolve()
    with pytest.raises(Exception):
        DocumentReference("../outside.pdf")
    with pytest.raises(DocumentPolicyError):
        DEFAULT_DOCUMENT_POLICY.validate(DocumentInput(DocumentReference("x.bin"), DocumentFormat.UNKNOWN, 1), tmp_path)


def test_extracted_content_is_always_untrusted():
    with pytest.raises(ValueError):
        ExtractedContent("content", DocumentReference("a.txt"), trusted=True)


def test_application_adapter_is_allowlisted_and_consequences_need_approval():
    manifest = ApplicationManifest("editor", "Editor", supported_operations=("inspect",), allowlisted=True)
    with pytest.raises(ValueError):
        ApplicationRequest("editor", "inspect", invocation=InvocationKind.CONSEQUENT)
    from autonomous_agent.application import DEFAULT_APPLICATION_POLICY
    DEFAULT_APPLICATION_POLICY.validate_request(ApplicationRequest("editor", "inspect"), manifest)
    with pytest.raises(ApplicationPolicyError):
        DEFAULT_APPLICATION_POLICY.validate_request(ApplicationRequest("editor", "other"), manifest)
