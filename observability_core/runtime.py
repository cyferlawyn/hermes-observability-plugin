"""Profile-routed SQLite connections for the collector and Desktop API."""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import store

log = logging.getLogger(__name__)
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[tuple[str, int]] = set()
_CONNECTION_LOCAL = threading.local()


def profile_home() -> Path:
    """Return the active profile home using Hermes' request/session routing context."""
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def profile_name() -> str | None:
    """Return the canonical active profile ID, or None when the home cannot be mapped safely."""
    from hermes_constants import profile_name_for_home

    return profile_name_for_home(profile_home())


def _configure_profile_connection(conn: sqlite3.Connection) -> None:
    """Apply Hermes database settings, with a telemetry-only WAL sync default."""
    conn.execute("PRAGMA busy_timeout = 350")
    conn.execute("PRAGMA foreign_keys = ON")
    configured_synchronous = None
    try:
        from hermes_cli.config import cfg_get, load_config_readonly
        from hermes_state_wal import apply_database_pragmas

        config = load_config_readonly() or {}
        configured_synchronous = cfg_get(config, "database", "synchronous", default=None)
        apply_database_pragmas(conn, db_label="state.db (observability plugin)")
    except ImportError:
        # Unit/standalone environments may not have Hermes' helper modules.
        pass
    except Exception:
        # A config lookup problem must not prevent observability from failing open.
        log.warning("observability: could not read database pragmas; retaining SQLite defaults", exc_info=True)
        return

    mode_row = conn.execute("PRAGMA journal_mode").fetchone()
    if not mode_row or str(mode_row[0]).lower() != "wal" or configured_synchronous is not None:
        return
    # The plugin writes only its own telemetry/reset rows on this connection.
    # NORMAL reduces hook fsync cost in WAL; any explicit database.synchronous
    # setting is honored above, and macOS keeps FULL for its documented safety floor.
    conn.execute("PRAGMA synchronous = FULL" if sys.platform == "darwin" else "PRAGMA synchronous = NORMAL")


def _close_thread_connection() -> None:
    cached = getattr(_CONNECTION_LOCAL, "entry", None)
    if cached is None:
        return
    try:
        cached[1].close()
    except sqlite3.Error:
        pass
    try:
        del _CONNECTION_LOCAL.entry
    except AttributeError:
        pass


def _thread_connection(
    db_path: Path, identity: tuple[str, int]
) -> tuple[sqlite3.Connection, tuple[str, int, int]]:
    cache_key = (*identity, os.getpid())
    cached = getattr(_CONNECTION_LOCAL, "entry", None)
    if cached is not None and cached[0] == cache_key:
        return cached[1], cache_key

    # Keep one connection per thread. Closing on profile/database identity changes
    # prevents a thread-local cache from retaining a connection to the prior profile.
    _close_thread_connection()
    conn = sqlite3.connect(str(db_path), timeout=0.35)
    conn.row_factory = sqlite3.Row
    try:
        _configure_profile_connection(conn)
    except Exception:
        conn.close()
        raise
    _CONNECTION_LOCAL.entry = (cache_key, conn)
    return conn, cache_key


def _discard_thread_connection(cache_key: tuple[str, int, int], conn: sqlite3.Connection) -> None:
    cached = getattr(_CONNECTION_LOCAL, "entry", None)
    if cached is not None and cached[0] == cache_key and cached[1] is conn:
        _close_thread_connection()


@contextmanager
def profile_connection() -> Iterator[sqlite3.Connection | None]:
    """Reuse one connected-profile state.db connection per thread."""
    db_path = profile_home() / "state.db"
    if not db_path.is_file():
        _close_thread_connection()
        yield None
        return

    stat = db_path.stat()
    identity = (str(db_path.resolve()), int(stat.st_ino))
    conn, cache_key = _thread_connection(db_path, identity)
    try:
        has_sessions = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
        ).fetchone()
        if not has_sessions:
            _discard_thread_connection(cache_key, conn)
            yield None
            return
        with _SCHEMA_LOCK:
            if identity not in _SCHEMA_READY:
                store.initialize(conn)
                _SCHEMA_READY.add(identity)
        yield conn
    except Exception:
        _discard_thread_connection(cache_key, conn)
        log.warning("observability: state.db access failed", exc_info=True)
        raise
