"""Read models shared by the Desktop API and tests."""
from __future__ import annotations

import sqlite3
from typing import Any

from . import source_detail, store


def overview(
    conn: sqlite3.Connection,
    *,
    session_limit: int = 100,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Return the Control room summary without reading message or provider payloads."""
    return {
        "available": True,
        "window": window,
        "observer_schema_version": store.OBSERVER_SCHEMA_VERSION,
        "telemetry_schema_version": store.SCHEMA_VERSION,
        "metrics": store.metrics(conn, window=window, now=now, timezone_name=timezone_name),
        "provider_attempts": store.provider_metrics(
            conn, window=window, now=now, timezone_name=timezone_name
        ),
        "approvals": store.approval_metrics(
            conn, window=window, now=now, timezone_name=timezone_name
        ),
        "sessions": store.list_sessions(
            conn, limit=session_limit, window=window, now=now, timezone_name=timezone_name
        ),
        "repeated_failures": store.repeated_failure_findings(
            conn, window=window, now=now, timezone_name=timezone_name
        ),
    }


def session_trace(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any] | None:
    """Return one source-session header and its captured observer metadata."""
    session = store.session_header(
        conn, session_id, window=window, now=now, timezone_name=timezone_name
    )
    if session is None:
        return None
    return {
        "window": window,
        "session": session,
        "calls": store.session_trace(conn, session_id),
        "provider_attempts": store.session_provider_attempts(conn, session_id),
        "provider_coverage": store.provider_session_coverage(conn, session_id),
        "approvals": store.session_approvals(conn, session_id),
        "approval_coverage": store.approval_session_coverage(conn, session_id),
    }


def call_detail(
    conn: sqlite3.Connection, session_id: str, call_id: str
) -> dict[str, Any] | None:
    """Join one observer call to canonical source messages on exact identifiers."""
    call = store.call_record(conn, session_id, call_id)
    if call is None:
        return None
    source = source_detail.resolve_call(
        conn, session_id, call["tool_name"], call["tool_call_id"]
    )
    context = source.pop("context", [])
    return {
        "available": source["status"] == "matched",
        "call": call,
        "source": source,
        "context": context,
    }
