from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ArtifactStoreLockTimeout(TimeoutError):
    """Raised when the canonical artifact-store write lock cannot be acquired."""


@contextmanager
def canonical_store_write_lock(
    database_path: str | Path,
    *,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.05,
) -> Iterator[None]:
    """Serialize canonical SQLite artifact-store writers across processes."""

    if str(database_path) == ":memory:":
        yield
        return

    lock_path = Path(database_path).with_suffix(Path(database_path).suffix + ".write.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if time.monotonic() >= deadline:
                    raise ArtifactStoreLockTimeout(
                        f"Timed out acquiring canonical artifact-store write lock: {lock_path}"
                    ) from error
                time.sleep(poll_seconds)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
