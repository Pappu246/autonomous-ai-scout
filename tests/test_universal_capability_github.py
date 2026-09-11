from __future__ import annotations

import pytest

from autonomous_agent.action_queue import PendingAction
from autonomous_agent.github_domain_connector import GitHubConnectorError, GitHubDomainConnector
from autonomous_agent.tool_registry import REGISTRY
from autonomous_agent.universal_capability import CapabilityRegistry, CapabilitySpec, Domain, IdempotencyMode, RetryPolicy, github_capabilities, web_capabilities
from autonomous_agent.web_domain_connector import WebConnectorError, WebResearchConnector

SHA = "a" * 40
BASE = "b" * 40


def test_universal_schema_is_complete_and_deterministic():
    spec = CapabilitySpec("github:inspect", Domain.GITHUB, "github.inspect", {"type": "object"}, {"type": "object"}, "low", "read_only", "required", "user_auth", "github", "none", "required", "required", ("repository:read",), IdempotencyMode.NATURAL, RetryPolicy(2, 1), True)
    assert spec.fingerprint == spec.fingerprint
    assert spec.domain is Domain.GITHUB and spec.version == 1


def test_authenticated_capability_cannot_embed_credential_material():
    with pytest.raises(ValueError):
        CapabilitySpec("github:bad", Domain.GITHUB, "github.inspect", {"type": "object"}, {"type": "object"}, "low", "read_only", "required", "user_auth", "token=secret", "none", "required", "required", ("repository:read",), IdempotencyMode.NATURAL, RetryPolicy(), True)


def test_write_capability_requires_approval():
    with pytest.raises(ValueError):
        CapabilitySpec("github:write", Domain.GITHUB, "github.change", {"type": "object"}, {"type": "object"}, "high", "controlled_write", "required", "user_auth", "github", "none", "required", "required", ("repository:change",), IdempotencyMode.REQUIRED, RetryPolicy(), True)


def test_github_registry_delegates_authority_to_existing_tool_registry():
    registry = github_capabilities(REGISTRY)
    assert {item.capability_id for item in registry.list(domain=Domain.GITHUB)} == {"github:change", "github:inspect", "github:status"}
    assert registry.authorize("github:inspect", ("inspect",)).allowed is True
    denied = registry.authorize("github:change", ("source_write",))
    assert denied.allowed is False and "permanently denied" in denied.reason


def test_disabled_capability_is_fail_closed():
    registry = CapabilityRegistry(REGISTRY)
    spec = CapabilitySpec("github:disabled", Domain.GITHUB, "github.inspect", {"type": "object"}, {"type": "object"}, "low", "read_only", "required", "user_auth", "github", "none", "required", "required", ("repository:read",), IdempotencyMode.NATURAL, RetryPolicy(), False)
    registry.register(spec)
    assert registry.authorize("github:disabled", ("inspect",)).allowed is False


def fake_fetch(path, params=None):
    if path == "/repos/owner/repo":
        return {"full_name": "owner/repo", "id": 1, "default_branch": "main", "archived": False, "fork": False}
    if path == "/repos/owner/repo/pulls/7":
        return {"state": "open", "draft": True, "base": {"ref": "main", "sha": BASE, "repo": {"full_name": "owner/repo"}}, "head": {"ref": "feature/x", "sha": SHA, "repo": {"full_name": "owner/repo"}}}
    if path == f"/repos/owner/repo/commits/{SHA}/check-runs":
        return {"check_runs": [{"name": "CI", "status": "completed", "conclusion": "success", "head_sha": SHA}]}
    raise AssertionError(path)


def test_github_connector_uses_verified_repository_scope_and_read_api():
    connector = GitHubDomainConnector(fake_fetch)
    assert connector.repository("owner/repo")["default_branch"] == "main"
    pr = connector.pull_request("owner/repo", 7)
    assert pr["head_sha"] == SHA and pr["base_sha"] == BASE
    assert connector.checks("owner/repo", SHA)[0]["conclusion"] == "success"


def test_github_connector_rejects_cross_repository_pr():
    def bad(path, params=None):
        return {"base": {"repo": {"full_name": "other/repo"}}, "head": {"repo": {"full_name": "owner/repo"}}}
    with pytest.raises(GitHubConnectorError):
        GitHubDomainConnector(bad).pull_request("owner/repo", 7)


def test_prepare_change_only_crosses_existing_approval_boundary():
    calls = []
    connector = GitHubDomainConnector(lambda path, params=None: calls.append(path) or {})
    action = PendingAction(id="act-1", task="fix safe lint issue", steps=("prepare source change",), risk="high", reason="requires review")
    request = connector.prepare_change(action, "owner/repo", "main", "feature/safe", "Fix lint", "Safe fix", "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n")
    assert request.repository == "owner/repo" and request.requires_approval is True
    assert calls == []


def test_web_capability_is_bound_to_existing_explicit_network_tool():
    registry = web_capabilities(REGISTRY)
    denied = registry.authorize("web:fetch", ("network",))
    assert denied.allowed is False and "permanently denied" in denied.reason
    approved = registry.authorize("web:fetch", ("network",), explicitly_approved=True)
    assert approved.allowed is True


def test_web_connector_rejects_credentials_and_unbounded_timeout():
    connector = WebResearchConnector(lambda url, params=None: {})
    with pytest.raises(WebConnectorError):
        connector.fetch("https://user:password@example.com")
    with pytest.raises(WebConnectorError):
        connector.fetch("https://example.com", timeout_seconds=31)


def test_web_connector_returns_minimal_structured_evidence():
    connector = WebResearchConnector(lambda url, params=None: {"status": 200, "content_type": "text/html", "text": "hello"})
    assert connector.fetch("https://example.com") == {"url": "https://example.com", "status": 200, "content_type": "text/html", "text": "hello"}
