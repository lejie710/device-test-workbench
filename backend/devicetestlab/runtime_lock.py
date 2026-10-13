"""Hold exclusive ownership of a runtime directory for one service lifespan.

The lock file intentionally remains after release. Removing it would let another
process lock a new file while an existing owner still holds the old file handle.
The operating system releases the actual lock if its owning process terminates.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import BinaryIO


class RuntimeOwnership:
    def __init__(self, runtime_dir: Path):
        self.path = Path(runtime_dir) / ".service.lock"
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        """Acquire immediately or reject startup before crash recovery changes data."""
        if self._handle is not None:
            raise RuntimeError("This service already owns its runtime directory.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            # Windows byte-range locking needs one byte at the locked position.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(
                    "The Device Test Lab runtime directory is already in use, or its "
                    "ownership lock is unavailable. Close the other service before "
                    f"starting another instance with this directory: {self.path.parent}"
                ) from exc
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
