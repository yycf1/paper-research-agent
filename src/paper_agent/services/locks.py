"""Serialize local file acquisition and deletion for the same paper within a server."""
from threading import Lock, RLock

_guard = Lock()
_locks: dict[tuple[str, str], RLock] = {}


def paper_lock(lib, sid):
    with _guard:
        return _locks.setdefault((str(lib.db_path.resolve()), sid), RLock())
