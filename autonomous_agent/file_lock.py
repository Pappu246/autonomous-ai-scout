from __future__ import annotations

import os
import time
from pathlib import Path


class FileLockTimeout(TimeoutError):
    """Raised when an inter-process file lock cannot be acquired in time."""


class InterProcessFileLock:
    """Portable advisory lock for coordinating file-backed state across processes."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 10.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        timeout = float(timeout_seconds)
        poll = float(poll_interval_seconds)
        if timeout < 0 or timeout > 300:
            raise ValueError("lock timeout is out of bounds")
        if poll <= 0 or poll > 1:
            raise ValueError("lock polling interval is out of bounds")
        self.path = Path(path)
        self.timeout_seconds = timeout
        self.poll_interval_seconds = poll
        self._handle = None
        self._remote_sync = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                self._acquire_windows()
            else:
                self._acquire_posix()

            # Vercel may start separate containers for separate HTTP requests.
            # Synchronize state files through private shared Blob storage while
            # holding both the local advisory lock and a distributed lease.
            if self.path.name.endswith(".lock"):
                target = self.path.with_name(self.path.name[:-5])
                from .vercel_state import VercelBlobStateSync

                remote_sync = VercelBlobStateSync(
                    target,
                    timeout_seconds=max(self.timeout_seconds, 10.0),
                    poll_interval_seconds=self.poll_interval_seconds,
                )
                if remote_sync.enabled:
                    remote_sync.__enter__()
                    self._remote_sync = remote_sync
        except Exception:
            if self._handle is not None:
                try:
                    if os.name == "nt":
                        import msvcrt
                        self._handle.seek(0)
                        try:
                            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    else:
                        import fcntl
                        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                finally:
                    self._handle.close()
                    self._handle = None
            raise
        return self

    def _acquire_posix(self) -> None:
        import fcntl

        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(
                    self._handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(f"timed out acquiring lock: {self.path}")
                time.sleep(self.poll_interval_seconds)

    def _acquire_windows(self) -> None:
        import msvcrt

        self._handle.seek(0)
        if self._handle.tell() == 0:
            self._handle.write(b"\0")
            self._handle.flush()
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(f"timed out acquiring lock: {self.path}")
                time.sleep(self.poll_interval_seconds)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        remote_error = None
        try:
            if self._remote_sync is not None:
                try:
                    self._remote_sync.__exit__(exc_type, exc_value, traceback)
                except Exception as error:
                    remote_error = error
                finally:
                    self._remote_sync = None
        finally:
            if self._handle is not None:
                try:
                    if os.name == "nt":
                        import msvcrt

                        self._handle.seek(0)
                        try:
                            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    else:
                        import fcntl

                        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                finally:
                    self._handle.close()
                    self._handle = None
        if remote_error is not None and exc_type is None:
            raise remote_error


__all__ = ["FileLockTimeout", "InterProcessFileLock"]
