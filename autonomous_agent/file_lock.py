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

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                self._acquire_windows()
            else:
                self._acquire_posix()
        except Exception:
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
        if self._handle is None:
            return
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


__all__ = ["FileLockTimeout", "InterProcessFileLock"]
