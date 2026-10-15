"""Profile-routed SQLite connections for the collector and Desktop API."""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import store

log = logging.getLogger(__name__)
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[tuple[str, int]] = set()


def profile_home() -> Path:
    """Return the active profile home using Hermes' request/session routing context."""
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def profile_name() -> str | None:
    """Return the canonical active profile ID, or None when the home cannot be mapped safely."""
    from hermes_constants import profile_name_for_home

    return profile_name_for_home(profile_home())


@contextmanager
def profile_connection() -> Iterator[sqlite3.Connection | None]:
    """Open the connected profile's existing state.db without creating a new database."""
    db_path = profile_home() / "state.db"
    if not db_path.is_file():
        yield None
        return

    stat = db_path.stat()
    identity = (str(db_path.resolve()), int(stat.st_ino))
    conn = sqlite3.connect(str(db_path), timeout=0.35)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 350")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        has_sessions = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
        ).fetchone()
        if not has_sessions:
            yield None
            return
        with _SCHEMA_LOCK:
            if identity not in _SCHEMA_READY:
                store.initialize(conn)
                _SCHEMA_READY.add(identity)
        yield conn
    except Exception:
        log.warning("observability: state.db access failed", exc_info=True)
        raise
    finally:
        conn.close()
