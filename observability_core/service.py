"""Read models shared by the Desktop API and tests."""
from __future__ import annotations

import sqlite3
from typing import Any

from . import store


def overview(conn: sqlite3.Connection, *, session_limit: int = 100) -> dict[str, Any]:
    """Return the Control room summary without reading message or provider payloads."""
    return {
        "available": True,
        "observer_schema_version": store.OBSERVER_SCHEMA_VERSION,
        "telemetry_schema_version": store.SCHEMA_VERSION,
        "metrics": store.metrics(conn),
        "sessions": store.list_sessions(conn, limit=session_limit),
    }


def session_trace(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    """Return one source-session header and its captured observer metadata."""
    session = next((item for item in store.list_sessions(conn, limit=500) if item["id"] == session_id), None)
    if session is None:
        return None
    return {
        "session": session,
        "calls": store.session_trace(conn, session_id),
    }
