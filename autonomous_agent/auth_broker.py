from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping

MAX_LEASE_SECONDS = 300


@dataclass(frozen=True)
class CredentialRef:
    provider: str
    subject: str
    resource: str
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.provider or not self.subject or not self.resource:
            raise ValueError("credential reference fields are required")
        if not self.scopes:
            raise ValueError("credential reference must include at least one scope")
        forbidden = ("token=", "password=", "secret=", "api_key=")
        joined = " ".join((self.provider, self.subject, self.resource, *self.scopes)).lower()
        if any(marker in joined for marker in forbidden):
            raise ValueError("credential reference must not contain credential material")


@dataclass(frozen=True)
class CredentialLease:
    reference: CredentialRef
    issued_at: str
    expires_at: str
    secret_handle: str

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(self.expires_at)


class PermissionBroker:
    """Checks requested permission scopes without storing credential material."""

    def authorize(self, requested: Iterable[str], granted: Iterable[str]) -> tuple[bool, str]:
        requested_set = {str(item).strip() for item in requested if str(item).strip()}
        granted_set = {str(item).strip() for item in granted if str(item).strip()}
        missing = sorted(requested_set - granted_set)
        if missing:
            return False, f"missing permission scopes: {', '.join(missing)}"
        return True, "permission scopes authorized"


CredentialProvider = Callable[[CredentialRef], str]


class CredentialBroker:
    """Resolve ephemeral credentials from an injected provider and never persist them."""

    def __init__(self, provider: CredentialProvider, permissions: PermissionBroker | None = None) -> None:
        self.provider = provider
        self.permissions = permissions or PermissionBroker()

    def acquire(
        self,
        reference: CredentialRef,
        *,
        granted_scopes: Iterable[str] = (),
        lease_seconds: int = 60,
    ) -> CredentialLease:
        allowed, reason = self.permissions.authorize(reference.scopes, granted_scopes)
        if not allowed:
            raise PermissionError(reason)
        duration = max(1, min(int(lease_seconds), MAX_LEASE_SECONDS))
        secret = self.provider(reference)
        if not isinstance(secret, str) or not secret:
            raise RuntimeError("credential provider returned no credential")
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=duration)
        handle = f"lease:{reference.provider}:{reference.subject}:{int(now.timestamp())}"
        return CredentialLease(reference, now.isoformat(), expires.isoformat(), handle)

    def with_credential(
        self,
        reference: CredentialRef,
        consumer: Callable[[str], object],
        *,
        granted_scopes: Iterable[str] = (),
    ) -> object:
        allowed, reason = self.permissions.authorize(reference.scopes, granted_scopes)
        if not allowed:
            raise PermissionError(reason)
        secret = self.provider(reference)
        if not isinstance(secret, str) or not secret:
            raise RuntimeError("credential provider returned no credential")
        return consumer(secret)


class AuthPermissionBoundary:
    """Coordinates tool metadata, permission scopes, and ephemeral credential access."""

    def __init__(self, credential_broker: CredentialBroker) -> None:
        self.credentials = credential_broker

    def check(
        self,
        reference: CredentialRef,
        *,
        granted_scopes: Iterable[str] = (),
    ) -> tuple[bool, str]:
        return self.credentials.permissions.authorize(reference.scopes, granted_scopes)


__all__ = ["AuthPermissionBoundary", "CredentialBroker", "CredentialLease", "CredentialRef", "PermissionBroker"]
