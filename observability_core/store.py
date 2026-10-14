"""Versioned, plugin-owned observability storage in a profile's state.db.

This module deliberately stores observer metadata only. It never copies tool
arguments/results, provider request/response payloads, or reasoning fields.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any

SCHEMA_VERSION = 1
OBSERVER_SCHEMA_VERSION = "hermes.observer.v1"
_META_TABLE = "hermes_observability_meta"
_SESSIONS_TABLE = "hermes_observability_sessions"
_CALLS_TABLE = "hermes_observability_calls"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {_META_TABLE} (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {_SESSIONS_TABLE} (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    captured_at REAL NOT NULL,
    observer_schema_version TEXT NOT NULL,
    coverage_state TEXT NOT NULL DEFAULT 'observed'
        CHECK (coverage_state IN ('observed', 'partial', 'unknown'))
);
CREATE TABLE IF NOT EXISTS {_CALLS_TABLE} (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    task_id TEXT,
    turn_id TEXT,
    api_request_id TEXT,
    tool_call_id TEXT,
    started_at REAL,
    completed_at REAL,
    duration_ms REAL,
    outcome TEXT NOT NULL DEFAULT 'unknown'
        CHECK (outcome IN ('ok', 'failed', 'not_executed', 'unknown')),
    error_type TEXT,
    error_message TEXT,
    capture_state TEXT NOT NULL DEFAULT 'start_only'
        CHECK (capture_state IN ('paired', 'start_only', 'post_only'))
);
CREATE INDEX IF NOT EXISTS hermes_observability_calls_session_idx
    ON {_CALLS_TABLE}(session_id, started_at, id);
CREATE INDEX IF NOT EXISTS hermes_observability_calls_tool_idx
    ON {_CALLS_TABLE}(session_id, tool_name, completed_at);
"""


class UnsupportedSchemaVersion(RuntimeError):
    """Raised when this plugin encounters telemetry written by a newer version."""


def initialize(conn: sqlite3.Connection) -> None:
    """Create or upgrade only this plugin's tables; never alter Hermes core tables."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(f"CREATE TABLE IF NOT EXISTS {_META_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
    row = conn.execute(f"SELECT value FROM {_META_TABLE} WHERE key = 'schema_version'").fetchone()
    current = int(row[0]) if row else 0
    if current > SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(
            f"telemetry schema {current} is newer than supported schema {SCHEMA_VERSION}"
        )
    conn.executescript(_SCHEMA)
    conn.execute(
        f"INSERT INTO {_META_TABLE}(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def register_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    captured_at: float | None = None,
    observer_schema_version: str = OBSERVER_SCHEMA_VERSION,
) -> bool:
    """Mark a session as eligible only when its Hermes session row already exists."""
    if not session_id or conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
        return False
    version = str(observer_schema_version or "unknown")
    conn.execute(
        f"INSERT INTO {_SESSIONS_TABLE} "
        "(session_id, captured_at, observer_schema_version, coverage_state) VALUES (?, ?, ?, 'observed') "
        "ON CONFLICT(session_id) DO UPDATE SET observer_schema_version = "
        f"CASE WHEN {_SESSIONS_TABLE}.observer_schema_version = excluded.observer_schema_version "
        f"THEN {_SESSIONS_TABLE}.observer_schema_version ELSE 'unknown' END",
        (session_id, float(captured_at if captured_at is not None else time.time()), version),
    )
    conn.commit()
    return True


def begin_tool_call(
    conn: sqlite3.Connection,
    session_id: str,
    tool_name: str,
    *,
    started_at: float | None = None,
    task_id: str | None = None,
    turn_id: str | None = None,
    api_request_id: str | None = None,
    tool_call_id: str | None = None,
) -> str | None:
    """Persist a metadata-only pending invocation for a session under observation."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return None
    call_id = uuid.uuid4().hex
    conn.execute(
        f"INSERT INTO {_CALLS_TABLE} "
        "(id, session_id, tool_name, task_id, turn_id, api_request_id, tool_call_id, "
        "started_at, outcome, capture_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 'start_only')",
        (call_id, session_id, str(tool_name or "unknown"), task_id, turn_id, api_request_id,
         tool_call_id, float(started_at if started_at is not None else time.time())),
    )
    conn.commit()
    return call_id


def normalize_outcome(status: Any) -> str:
    """Map only explicit observer statuses; never infer intent success from result text."""
    value = str(status or "").strip().lower()
    if value in {"ok", "success"}:
        return "ok"
    if value in {"error", "failed", "failure"}:
        return "failed"
    if value in {"blocked", "cancelled", "canceled"}:
        return "not_executed"
    return "unknown"


def _bounded(value: Any, limit: int = 2000) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


def complete_tool_call(
    conn: sqlite3.Connection,
    call_id: str,
    status: Any,
    *,
    completed_at: float | None = None,
    duration_ms: float | None = None,
    error_type: Any = None,
    error_message: Any = None,
) -> bool:
    """Close one started invocation using only the explicit observer outcome."""
    cursor = conn.execute(
        f"UPDATE {_CALLS_TABLE} SET completed_at = ?, duration_ms = ?, outcome = ?, "
        "error_type = ?, error_message = ?, capture_state = 'paired' "
        "WHERE id = ? AND completed_at IS NULL",
        (float(completed_at if completed_at is not None else time.time()), duration_ms,
         normalize_outcome(status), _bounded(error_type, 256), _bounded(error_message), call_id),
    )
    conn.commit()
    return cursor.rowcount == 1


def record_post_only_call(
    conn: sqlite3.Connection,
    session_id: str,
    tool_name: str,
    status: Any,
    *,
    completed_at: float | None = None,
    duration_ms: float | None = None,
    task_id: str | None = None,
    turn_id: str | None = None,
    api_request_id: str | None = None,
    tool_call_id: str | None = None,
    error_type: Any = None,
    error_message: Any = None,
) -> str | None:
    """Record a post hook that had no matching start as visible, partial evidence."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return None
    if tool_call_id:
        existing = conn.execute(
            f"SELECT id FROM {_CALLS_TABLE} WHERE session_id = ? AND tool_call_id = ? "
            "AND COALESCE(turn_id, '') = COALESCE(?, '') "
            "AND COALESCE(api_request_id, '') = COALESCE(?, '') "
            "AND completed_at IS NOT NULL LIMIT 1",
            (session_id, tool_call_id, turn_id, api_request_id),
        ).fetchone()
        if existing:
            return str(existing[0])
    call_id = uuid.uuid4().hex
    conn.execute(
        f"INSERT INTO {_CALLS_TABLE} "
        "(id, session_id, tool_name, task_id, turn_id, api_request_id, tool_call_id, "
        "completed_at, duration_ms, outcome, error_type, error_message, capture_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'post_only')",
        (call_id, session_id, str(tool_name or "unknown"), task_id, turn_id, api_request_id,
         tool_call_id, float(completed_at if completed_at is not None else time.time()), duration_ms,
         normalize_outcome(status), _bounded(error_type, 256), _bounded(error_message)),
    )
    conn.commit()
    return call_id


def _coverage_state(conn: sqlite3.Connection, session_id: str, observer_schema_version: str | None) -> str:
    if observer_schema_version != OBSERVER_SCHEMA_VERSION:
        return "unknown"
    gap = conn.execute(
        f"SELECT 1 FROM {_CALLS_TABLE} WHERE session_id = ? "
        "AND (capture_state != 'paired' OR outcome = 'unknown') LIMIT 1",
        (session_id,),
    ).fetchone()
    return "partial" if gap else "observed_no_known_gaps"


def session_trace(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Return observer metadata only; message content remains in Hermes session storage."""
    session = conn.execute(
        f"SELECT observer_schema_version FROM {_SESSIONS_TABLE} WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not session:
        return []
    rows = conn.execute(
        f"SELECT id, session_id, tool_name, task_id, turn_id, api_request_id, tool_call_id, "
        "started_at, completed_at, duration_ms, outcome, error_type, error_message, capture_state "
        f"FROM {_CALLS_TABLE} WHERE session_id = ? ORDER BY COALESCE(started_at, completed_at), id",
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def call_record(conn: sqlite3.Connection, session_id: str, call_id: str) -> dict[str, Any] | None:
    """Return one captured call's metadata, scoped to its exact source session."""
    row = conn.execute(
        f"SELECT id, session_id, tool_name, tool_call_id, started_at, completed_at, "
        f"duration_ms, outcome, error_type, error_message FROM {_CALLS_TABLE} "
        "WHERE session_id = ? AND id = ?",
        (session_id, call_id),
    ).fetchone()
    return dict(row) if row else None


def list_sessions(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """List session headers and explicit coverage labels, newest first."""
    limit = max(1, min(int(limit), 500))
    rows = conn.execute(
        f"SELECT s.id, s.title, s.started_at, s.ended_at, o.captured_at, "
        f"o.observer_schema_version FROM sessions s LEFT JOIN {_SESSIONS_TABLE} o "
        "ON o.session_id = s.id ORDER BY s.started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        version = item.pop("observer_schema_version")
        item["coverage_state"] = (
            "not_captured" if item["captured_at"] is None
            else _coverage_state(conn, item["id"], version)
        )
        item["call_count"] = conn.execute(
            f"SELECT COUNT(*) FROM {_CALLS_TABLE} WHERE session_id = ?", (item["id"],)
        ).fetchone()[0]
        result.append(item)
    return result


def metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate observed counts; calculate rates only over compatible, gap-free sessions."""
    counts = {"total": 0, "ok": 0, "failed": 0, "not_executed": 0, "unknown": 0}
    for row in conn.execute(f"SELECT outcome, COUNT(*) AS n FROM {_CALLS_TABLE} GROUP BY outcome"):
        key = row["outcome"]
        if key in counts:
            counts[key] = int(row["n"])
            counts["total"] += int(row["n"])

    rate_counts = {"ok": 0, "failed": 0, "denominator": 0}
    compatible = (
        f"SELECT o.session_id FROM {_SESSIONS_TABLE} o "
        "WHERE o.observer_schema_version = ? AND NOT EXISTS ("
        f"SELECT 1 FROM {_CALLS_TABLE} c WHERE c.session_id = o.session_id "
        "AND (c.capture_state != 'paired' OR c.outcome = 'unknown'))"
    )
    for row in conn.execute(
        f"SELECT c.outcome, COUNT(*) AS n FROM {_CALLS_TABLE} c "
        f"JOIN ({compatible}) eligible ON eligible.session_id = c.session_id "
        "WHERE c.outcome IN ('ok', 'failed') GROUP BY c.outcome",
        (OBSERVER_SCHEMA_VERSION,),
    ):
        rate_counts[row["outcome"]] = int(row["n"])
    rate_counts["denominator"] = rate_counts["ok"] + rate_counts["failed"]

    total_sessions = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
    captured_sessions = int(conn.execute(f"SELECT COUNT(*) FROM {_SESSIONS_TABLE}").fetchone()[0])
    observed_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM ({compatible})", (OBSERVER_SCHEMA_VERSION,)
    ).fetchone()[0])
    partial_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM {_SESSIONS_TABLE} o WHERE o.observer_schema_version = ? "
        f"AND EXISTS (SELECT 1 FROM {_CALLS_TABLE} c WHERE c.session_id = o.session_id "
        "AND (c.capture_state != 'paired' OR c.outcome = 'unknown'))",
        (OBSERVER_SCHEMA_VERSION,),
    ).fetchone()[0])
    unknown_sessions = max(0, captured_sessions - observed_sessions - partial_sessions)
    success_rate = (
        rate_counts["ok"] / rate_counts["denominator"] if rate_counts["denominator"] else None
    )
    return {
        "calls": counts,
        "rate_calls": rate_counts,
        "success_rate": success_rate,
        "total_sessions": total_sessions,
        "captured_sessions": captured_sessions,
        "observed_sessions": observed_sessions,
        "partial_sessions": partial_sessions,
        "unknown_sessions": unknown_sessions,
        "excluded_sessions": max(0, total_sessions - captured_sessions),
        "rate_excluded_sessions": max(0, total_sessions - observed_sessions),
    }
