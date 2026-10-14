from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


STATE_BLOB_PREFIX = "scout-state/files/"
LOCK_BLOB_PREFIX = "scout-state/locks/"
_LOCK_STALE_SECONDS = 120.0


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
        self.poll_interval_seconds = max(0.01, min(float(poll_interval_seconds), 0.5))
        self.key = state_blob_key(self.target)
        self.lock_key = (
            LOCK_BLOB_PREFIX + hashlib.sha256(self.key.encode("utf-8")).hexdigest() + ".json"
            if self.key
            else None
        )
        self.client: Any = None
        self._baseline: bytes | None = None
        self._remote_missing = True
        self._lock_acquired = False

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
            {"created_at": time.time(), "target": self.key},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        client = self._blob_client()
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
                self._lock_acquired = True
                return
            except Exception as acquire_error:
                # A conflicting lease is expected under contention. Verify that
                # the lease exists before retrying; propagate unrelated failures.
                try:
                    current = self._get_bytes(self.lock_key)
                except Exception:
                    current = None
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
                        except Exception:
                            pass
                        continue
                elif time.monotonic() >= deadline:
                    raise TimeoutError("shared state lock could not be acquired") from acquire_error
                if time.monotonic() >= deadline:
                    raise TimeoutError("shared state lock timed out; another runtime may still be updating state") from acquire_error
                time.sleep(self.poll_interval_seconds)

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
                self._baseline = self.target.read_bytes() if self.target.exists() else None
                self._remote_missing = True
        except Exception:
            self._release_remote_lock()
            raise
        return self

    def _release_remote_lock(self) -> None:
        if not self._lock_acquired or self.lock_key is None:
            return
        try:
            self._blob_client().delete(self.lock_key, token=self.token)
        finally:
            self._lock_acquired = False

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        persistence_error: Exception | None = None
        if self.enabled:
            try:
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
