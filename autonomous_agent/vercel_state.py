from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any


STATE_BLOB_PREFIX = "scout-state/files/"
LOCK_BLOB_PREFIX = "scout-state/locks/"
# The existing PR #280 Preview has history under the legacy preview/ prefix.
# Keep that one branch readable; new Preview branches use isolated prefixes.
# Retire this compatibility mapping only with an owner-approved state migration.
LEGACY_PREVIEW_REF = "fix/rebased-mission-and-windows-20261012"
_LOCK_STALE_SECONDS = 120.0

_RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def _http_status_code(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        status = getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


def _is_retryable_storage_error(error: BaseException) -> bool:
    return _http_status_code(error) in _RETRYABLE_HTTP_STATUSES


def state_blob_key(path: str | Path) -> str | None:
    """Map a local state file to a stable Blob pathname without host-specific roots."""
    target = Path(path)
    parts = target.parts
    state_indexes = [index for index, part in enumerate(parts) if part == "state"]
    if not state_indexes:
        return None
    relative = Path(*parts[state_indexes[-1] + 1 :]).as_posix()
    if relative in {"", "."} or relative.startswith("../"):
        return None
    # Keep deployment environments isolated while sharing state across all
    # instances and redeployments within the same Vercel environment.
    scope = os.getenv("VERCEL_ENV", "local").strip().lower()
    if scope not in {"production", "preview", "development", "local"}:
        scope = "local"
    if scope == "preview":
        branch_ref = os.getenv("VERCEL_GIT_COMMIT_REF", "").strip()
        if not branch_ref:
            raise RuntimeError("preview shared state requires a Vercel branch ref")
        if branch_ref != LEGACY_PREVIEW_REF:
            digest = hashlib.sha256(branch_ref.encode("utf-8")).hexdigest()[:24]
            scope = f"preview/branches/{digest}"
    return STATE_BLOB_PREFIX + scope + "/" + relative


class VercelBlobStateSync:
    """Cross-instance file synchronization for state files protected by InterProcessFileLock.

    Each state file is one private blob. A small create-if-absent lease blob provides
    a distributed read/modify/write lock across Vercel runtime instances. Local mode
    never imports or requires the Vercel SDK.
    """

    def __init__(
        self,
        target: str | Path,
        *,
        token: str | None = None,
        timeout_seconds: float = 20.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self.target = Path(target)
        self.token = token or os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self.poll_interval_seconds = max(0.1, min(float(poll_interval_seconds), 0.5))
        self.key = state_blob_key(self.target) if self.token else None
        self.lock_key = (
            LOCK_BLOB_PREFIX + hashlib.sha256(self.key.encode("utf-8")).hexdigest() + ".json"
            if self.key
            else None
        )
        self.client: Any = None
        self._baseline: bytes | None = None
        self._remote_missing = True
        self._lock_acquired = False
        self._lock_payload: bytes | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.key and self.lock_key)

    def _blob_client(self):
        if self.client is None:
            from vercel.blob import BlobClient

            self.client = BlobClient(token=self.token)
        return self.client

    def _get_bytes(self, pathname: str) -> bytes | None:
        # The Vercel Blob Python SDK raises BlobNotFoundError (it does not return
        # None) for a missing private object. Treat only that documented case as
        # an empty first-use store; auth/network/server failures must still fail closed.
        from vercel.blob import BlobNotFoundError

        try:
            result = self._blob_client().get(
                pathname,
                access="private",
                use_cache=False,
                token=self.token,
            )
        except BlobNotFoundError:
            return None
        if result is None:
            return None
        content = getattr(result, "content", None)
        if content is None:
            raise RuntimeError("private state store returned no blob content")
        return bytes(content)

    def _put_bytes(self, pathname: str, content: bytes, *, overwrite: bool) -> None:
        self._blob_client().put(
            pathname,
            content,
            access="private",
            content_type="application/json",
            add_random_suffix=False,
            overwrite=overwrite,
            token=self.token,
        )

    def _acquire_remote_lock(self) -> None:
        assert self.lock_key is not None
        deadline = time.monotonic() + self.timeout_seconds
        payload = json.dumps(
            {"created_at": time.time(), "target": self.key, "owner": secrets.token_hex(16)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        client = self._blob_client()
        delay = self.poll_interval_seconds
        while True:
            try:
                client.put(
                    self.lock_key,
                    payload,
                    access="private",
                    content_type="application/json",
                    add_random_suffix=False,
                    overwrite=False,
                    token=self.token,
                )
                self._lock_payload = payload
                self._lock_acquired = True
                return
            except Exception as acquire_error:
                # Only an explicit create-if-absent conflict means another
                # runtime may hold the lease. Retry temporary upstream errors
                # with bounded backoff; fail fast for auth/configuration errors.
                if "already exists" not in str(acquire_error).lower():
                    if _is_retryable_storage_error(acquire_error) and time.monotonic() < deadline:
                        time.sleep(delay)
                        delay = min(delay * 1.5, 0.5)
                        continue
                    if _is_retryable_storage_error(acquire_error):
                        raise TimeoutError("shared state lock timed out after transient storage errors") from acquire_error
                    raise RuntimeError("private shared-state lease could not be created") from acquire_error
                try:
                    current = self._get_bytes(self.lock_key)
                except Exception as verification_error:
                    if _is_retryable_storage_error(verification_error) and time.monotonic() < deadline:
                        time.sleep(delay)
                        delay = min(delay * 1.5, 0.5)
                        continue
                    raise RuntimeError("private shared-state lease could not be verified") from verification_error
                if current == payload:
                    # The first PUT may have succeeded even if its response was
                    # lost. An unpredictable owner tag makes this our own lease.
                    self._lock_payload = payload
                    self._lock_acquired = True
                    return
                if current is not None:
                    stale = False
                    try:
                        metadata = json.loads(current.decode("utf-8"))
                        created = float(metadata.get("created_at", 0))
                        stale = created > 0 and time.time() - created > _LOCK_STALE_SECONDS
                    except (UnicodeDecodeError, ValueError, TypeError, AttributeError):
                        stale = True
                    if stale:
                        try:
                            client.delete(self.lock_key, token=self.token)
                        except Exception as delete_error:
                            # A lease disappearing between GET and DELETE is
                            # already released; rate limits and server failures
                            # are not. Never tight-loop on failed DELETEs.
                            if _http_status_code(delete_error) != 404:
                                if _is_retryable_storage_error(delete_error) and time.monotonic() < deadline:
                                    time.sleep(delay)
                                    delay = min(delay * 1.5, 0.5)
                                    continue
                                raise RuntimeError("stale shared-state lease could not be removed") from delete_error
                        time.sleep(min(delay, 0.1))
                        continue
                # A race can delete the competing lease between the failed PUT
                # and this GET (or Blob reads can briefly lag writes). Retry with
                # exponential backoff instead of a request storm under contention.
                if time.monotonic() >= deadline:
                    raise TimeoutError("shared state lock timed out; another runtime may still be updating state") from acquire_error
                time.sleep(delay)
                delay = min(delay * 1.5, 0.5)

    def __enter__(self):
        if not self.enabled:
            return self
        self._acquire_remote_lock()
        try:
            remote = self._get_bytes(self.key)  # type: ignore[arg-type]
            self.target.parent.mkdir(parents=True, exist_ok=True)
            if remote is not None:
                self.target.write_bytes(remote)
                self._baseline = remote
                self._remote_missing = False
            else:
                # A local copy from an earlier container request is not proof
                # that the shared object is still current. Never resurrect it
                # after a missing remote read (especially approval claims).
                if self.target.exists():
                    raise RuntimeError("private shared-state snapshot missing while local copy exists; manual reconciliation required")
                self._baseline = None
                self._remote_missing = True
        except Exception:
            self._release_remote_lock()
            raise
        return self

    def _owns_remote_lock(self) -> bool:
        return bool(
            self._lock_acquired and self.lock_key is not None
            and self._lock_payload is not None
            and self._get_bytes(self.lock_key) == self._lock_payload
        )

    def _release_remote_lock(self) -> None:
        if not self._lock_acquired or self.lock_key is None:
            return
        try:
            # A stale lease could have been replaced by another runtime. Do
            # not delete that runtime's lease during our cleanup.
            if not self._owns_remote_lock():
                return
            try:
                self._blob_client().delete(self.lock_key, token=self.token)
            except Exception as error:
                from vercel.blob import BlobNotFoundError

                # Lease release is idempotent: another runtime may have removed
                # a stale lease between our last operation and cleanup.
                if not isinstance(error, BlobNotFoundError) and _http_status_code(error) != 404:
                    raise
        finally:
            self._lock_acquired = False
            self._lock_payload = None

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        persistence_error: Exception | None = None
        if self.enabled:
            try:
                if not self._owns_remote_lock():
                    raise RuntimeError("shared state lease ownership lost; refusing to publish")
                current = self.target.read_bytes() if self.target.exists() else None
                changed = current != self._baseline
                if current is None:
                    if not self._remote_missing:
                        self._blob_client().delete(self.key, token=self.token)  # type: ignore[arg-type]
                elif changed or self._remote_missing:
                    self._put_bytes(self.key, current, overwrite=True)  # type: ignore[arg-type]
            except Exception as error:
                persistence_error = error
            finally:
                try:
                    self._release_remote_lock()
                except Exception as error:
                    if persistence_error is None:
                        persistence_error = error
        if persistence_error is not None and exc_type is None:
            raise RuntimeError("shared state could not be persisted to private Vercel Blob") from persistence_error


__all__ = ["VercelBlobStateSync", "state_blob_key"]
