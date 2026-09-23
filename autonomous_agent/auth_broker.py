from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable, Iterable
from uuid import uuid4

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
    """Resolve scoped credentials into short-lived in-memory, single-use lease handles."""

    def __init__(self, provider: CredentialProvider, permissions: PermissionBroker | None = None) -> None:
        self.provider = provider
        self.permissions = permissions or PermissionBroker()
        self._leases: dict[str, tuple[str, datetime]] = {}
        self._lock = Lock()

    def purge_expired(self) -> int:
        now = datetime.now(timezone.utc)
        with self._lock:
            expired = [handle for handle, (_, expires) in self._leases.items() if now >= expires]
            for handle in expired:
                self._leases.pop(handle, None)
            return len(expired)

    def acquire(
        self,
        reference: CredentialRef,
        *,
        granted_scopes: Iterable[str] = (),
        lease_seconds: int = 60,
    ) -> CredentialLease:
        self.purge_expired()
        allowed, reason = self.permissions.authorize(reference.scopes, granted_scopes)
        if not allowed:
            raise PermissionError(reason)
        duration = max(1, min(int(lease_seconds), MAX_LEASE_SECONDS))
        secret = self.provider(reference)
        if not isinstance(secret, str) or not secret:
            raise RuntimeError("credential provider returned no credential")
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=duration)
        handle = f"lease:{uuid4().hex}"
        with self._lock:
            self._leases[handle] = (secret, expires)
        return CredentialLease(reference, now.isoformat(), expires.isoformat(), handle)

    def use_lease(
        self,
        lease: CredentialLease,
        consumer: Callable[[str], object],
    ) -> object:
        self.purge_expired()
        now = datetime.now(timezone.utc)
        if now >= datetime.fromisoformat(lease.expires_at):
            with self._lock:
                self._leases.pop(lease.secret_handle, None)
            raise PermissionError("credential lease has expired")
        with self._lock:
            record = self._leases.pop(lease.secret_handle, None)
        if record is None:
            raise PermissionError("credential lease is invalid or already consumed")
        secret, expires = record
        if now >= expires:
            raise PermissionError("credential lease has expired")
        return consumer(secret)

    def revoke_lease(self, lease: CredentialLease) -> bool:
        with self._lock:
            return self._leases.pop(lease.secret_handle, None) is not None

    def with_credential(
        self,
        reference: CredentialRef,
        consumer: Callable[[str], object],
        *,
        granted_scopes: Iterable[str] = (),
    ) -> object:
        lease = self.acquire(reference, granted_scopes=granted_scopes, lease_seconds=60)
        return self.use_lease(lease, consumer)


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
