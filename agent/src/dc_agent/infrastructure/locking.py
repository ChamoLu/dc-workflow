"""Short cross-process publication locks using only the standard library."""

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ..domain import AgentError

_guard = threading.Lock()
_threads: dict[str, threading.RLock] = {}


@contextmanager
def publication_lock(path: Path, timeout: float = 15):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _guard:
        local = _threads.setdefault(str(path.resolve()), threading.RLock())
    if not local.acquire(timeout=timeout):
        raise AgentError("ASSET_BUSY", "Asset publication is busy", retryable=True)
    handle = None
    locked = False
    try:
        handle = path.open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AgentError(
                        "ASSET_BUSY", "Asset publication is busy", retryable=True
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        if handle:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        local.release()
