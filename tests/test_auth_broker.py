from datetime import datetime, timezone
from pathlib import Path

import pytest

from autonomous_agent.auth_broker import CredentialBroker, CredentialRef, PermissionBroker


def test_permission_broker_requires_all_scopes():
    broker = PermissionBroker()
    assert broker.authorize(("mail:read",), ("mail:read",))[0]
    allowed, reason = broker.authorize(("mail:send",), ("mail:read",))
    assert not allowed
    assert "mail:send" in reason


def test_credential_ref_rejects_embedded_secret_material():
    with pytest.raises(ValueError):
        CredentialRef("gmail", "user", "token=SECRET", ("mail:read",))


def test_credential_broker_issues_bounded_ephemeral_lease_without_secret_in_handle():
    reference = CredentialRef("gmail", "demo-user", "primary", ("mail:read",))
    calls = []

    def provider(ref):
        calls.append(ref)
        return "SUPERSECRET"

    broker = CredentialBroker(provider)
    lease = broker.acquire(reference, granted_scopes=["mail:read"], lease_seconds=9999)
    assert calls == [reference]
    assert not lease.expired
    assert "SUPERSECRET" not in lease.secret_handle
    expires = datetime.fromisoformat(lease.expires_at) - datetime.fromisoformat(lease.issued_at)
    assert expires.total_seconds() <= 300


def test_credential_access_is_denied_without_scope():
    broker = CredentialBroker(lambda _: "SUPERSECRET")
    reference = CredentialRef("gmail", "demo-user", "primary", ("mail:send",))
    with pytest.raises(PermissionError, match="mail:send"):
        broker.acquire(reference, granted_scopes=["mail:read"])


def test_with_credential_passes_secret_only_to_consumer_and_persists_nothing(tmp_path: Path):
    reference = CredentialRef("github", "demo-user", "primary", ("repo:read",))
    broker = CredentialBroker(lambda _: "SUPERSECRET")
    observed = []
    result = broker.with_credential(
        reference, lambda secret: observed.append(secret) or "done", granted_scopes=["repo:read"]
    )
    assert result == "done"
    assert observed == ["SUPERSECRET"]
    assert list(tmp_path.iterdir()) == []
