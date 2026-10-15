"""Versioned, plugin-owned observability storage in a profile's state.db.

This module deliberately stores observer metadata only. It never copies tool
arguments/results, provider request/response payloads, or reasoning fields.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import repeated_failures

SCHEMA_VERSION = 5
OBSERVER_SCHEMA_VERSION = "hermes.observer.v1"
_META_TABLE = "hermes_observability_meta"
_SESSIONS_TABLE = "hermes_observability_sessions"
_CALLS_TABLE = "hermes_observability_calls"
_PROVIDER_ATTEMPTS_TABLE = "hermes_observability_provider_attempts"
_FAMILY_COVERAGE_TABLE = "hermes_observability_family_coverage"
_APPROVALS_TABLE = "hermes_observability_approvals"
_SUBAGENTS_TABLE = "hermes_observability_subagents"

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
    error_code TEXT,
    error_message TEXT,
    failure_status TEXT,
    capture_state TEXT NOT NULL DEFAULT 'start_only'
        CHECK (capture_state IN ('paired', 'start_only', 'post_only'))
);
CREATE INDEX IF NOT EXISTS hermes_observability_calls_session_idx
    ON {_CALLS_TABLE}(session_id, started_at, id);
CREATE INDEX IF NOT EXISTS hermes_observability_calls_tool_idx
    ON {_CALLS_TABLE}(session_id, tool_name, completed_at);
CREATE TABLE IF NOT EXISTS {_FAMILY_COVERAGE_TABLE} (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event_family TEXT NOT NULL,
    observer_schema_version TEXT NOT NULL,
    support_state TEXT NOT NULL CHECK (support_state IN ('supported', 'unsupported', 'unknown')),
    PRIMARY KEY (session_id, event_family)
);
CREATE TABLE IF NOT EXISTS {_PROVIDER_ATTEMPTS_TABLE} (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    task_id TEXT,
    turn_id TEXT,
    api_request_id TEXT,
    api_call_count INTEGER,
    provider TEXT,
    model TEXT,
    started_at REAL,
    ended_at REAL,
    api_duration REAL,
    outcome TEXT NOT NULL DEFAULT 'unknown' CHECK (outcome IN ('ok', 'failed', 'unknown')),
    status_code INTEGER,
    retry_count INTEGER,
    max_retries INTEGER,
    retryable INTEGER CHECK (retryable IN (0, 1) OR retryable IS NULL),
    error_type TEXT,
    response_model TEXT,
    finish_reason TEXT,
    usage TEXT NOT NULL DEFAULT '{{}}',
    observer_schema_version TEXT NOT NULL,
    capture_state TEXT NOT NULL DEFAULT 'start_only'
        CHECK (capture_state IN ('paired', 'start_only', 'post_only'))
);
CREATE INDEX IF NOT EXISTS hermes_observability_provider_session_idx
    ON {_PROVIDER_ATTEMPTS_TABLE}(session_id, started_at, id);
CREATE INDEX IF NOT EXISTS hermes_observability_provider_turn_idx
    ON {_PROVIDER_ATTEMPTS_TABLE}(session_id, turn_id, api_call_count);
CREATE TABLE IF NOT EXISTS {_APPROVALS_TABLE} (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    session_key TEXT,
    turn_id TEXT,
    tool_call_id TEXT,
    surface TEXT,
    pattern_key TEXT,
    pattern_keys TEXT NOT NULL DEFAULT '[]',
    requested_at REAL,
    decided_at REAL,
    duration_ms REAL,
    outcome TEXT NOT NULL DEFAULT 'unknown'
        CHECK (outcome IN ('allowed', 'denied', 'timed_out', 'cancelled', 'unknown')),
    choice TEXT,
    decided_by TEXT,
    observer_schema_version TEXT NOT NULL,
    capture_state TEXT NOT NULL CHECK (capture_state IN ('paired', 'request_only', 'response_only'))
);
CREATE INDEX IF NOT EXISTS hermes_observability_approvals_session_idx
    ON {_APPROVALS_TABLE}(session_id, requested_at, decided_at, id);
CREATE TABLE IF NOT EXISTS {_SUBAGENTS_TABLE} (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    parent_session_id TEXT,
    child_session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    child_session_ref TEXT,
    parent_turn_id TEXT,
    parent_subagent_id TEXT,
    child_subagent_id TEXT,
    child_role TEXT,
    child_goal TEXT,
    started_at REAL,
    stopped_at REAL,
    duration_ms REAL,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('succeeded', 'failed', 'interrupted', 'unknown')),
    child_summary TEXT,
    observer_schema_version TEXT NOT NULL,
    capture_state TEXT NOT NULL CHECK (capture_state IN ('paired', 'start_only', 'stop_only'))
);
CREATE INDEX IF NOT EXISTS hermes_observability_subagents_parent_idx
    ON {_SUBAGENTS_TABLE}(session_id, started_at, id);
CREATE INDEX IF NOT EXISTS hermes_observability_subagents_child_idx
    ON {_SUBAGENTS_TABLE}(child_session_id, started_at, id);
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
    call_columns = {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({_CALLS_TABLE})")
    }
    if "error_code" not in call_columns:
        conn.execute(f"ALTER TABLE {_CALLS_TABLE} ADD COLUMN error_code TEXT")
    if "failure_status" not in call_columns:
        conn.execute(f"ALTER TABLE {_CALLS_TABLE} ADD COLUMN failure_status TEXT")
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


def register_provider_family(
    conn: sqlite3.Connection, session_id: str, observer_schema_version: str
) -> bool:
    """Record API-family support independently from tool-call coverage."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return False
    version = str(observer_schema_version or "unknown")
    support = "supported" if version == OBSERVER_SCHEMA_VERSION else "unknown"
    conn.execute(
        f"INSERT INTO {_FAMILY_COVERAGE_TABLE} "
        "(session_id, event_family, observer_schema_version, support_state) VALUES (?, 'provider_api', ?, ?) "
        "ON CONFLICT(session_id, event_family) DO UPDATE SET "
        "observer_schema_version = CASE WHEN observer_schema_version = excluded.observer_schema_version "
        "THEN observer_schema_version ELSE 'unknown' END, "
        "support_state = CASE WHEN observer_schema_version = excluded.observer_schema_version "
        "THEN support_state ELSE 'unknown' END",
        (session_id, version, support),
    )
    conn.commit()
    return True


def register_approval_family(
    conn: sqlite3.Connection, session_id: str, observer_schema_version: str
) -> bool:
    """Record approval observer support independently from tool-call coverage."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return False
    version = str(observer_schema_version or "unknown")
    support = "supported" if version == OBSERVER_SCHEMA_VERSION else "unknown"
    conn.execute(
        f"INSERT INTO {_FAMILY_COVERAGE_TABLE} "
        "(session_id, event_family, observer_schema_version, support_state) "
        "VALUES (?, 'approval', ?, ?) ON CONFLICT(session_id, event_family) DO UPDATE SET "
        "observer_schema_version = CASE WHEN observer_schema_version = excluded.observer_schema_version "
        "THEN observer_schema_version ELSE 'unknown' END, "
        "support_state = CASE WHEN observer_schema_version = excluded.observer_schema_version "
        "THEN support_state ELSE 'unknown' END",
        (session_id, version, support),
    )
    conn.commit()
    return True


def register_subagent_family(
    conn: sqlite3.Connection, session_id: str, observer_schema_version: str
) -> bool:
    """Record subagent-family support independently from tools and other events."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return False
    version = str(observer_schema_version or "unknown")
    support = "supported" if version == OBSERVER_SCHEMA_VERSION else "unknown"
    conn.execute(
        f"INSERT INTO {_FAMILY_COVERAGE_TABLE} "
        "(session_id, event_family, observer_schema_version, support_state) "
        "VALUES (?, 'subagent', ?, ?) ON CONFLICT(session_id, event_family) DO UPDATE SET "
        "observer_schema_version = CASE WHEN observer_schema_version = excluded.observer_schema_version "
        "THEN observer_schema_version ELSE 'unknown' END, "
        "support_state = CASE WHEN observer_schema_version = excluded.observer_schema_version "
        "THEN support_state ELSE 'unknown' END",
        (session_id, version, support),
    )
    conn.commit()
    return True


def _subagent_status(value: Any) -> str:
    status = value.strip().lower() if isinstance(value, str) else ""
    if status in {"success", "succeeded", "completed", "complete"}:
        return "succeeded"
    if status in {"error", "failed", "failure"}:
        return "failed"
    if status in {"interrupted", "cancelled", "canceled", "timeout", "timed_out", "timed-out"}:
        return "interrupted"
    return "unknown"


def _subagent_identity(value: Any) -> str | None:
    """Treat empty or whitespace-only lifecycle identifiers as unavailable."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value)
    return text if text.strip() else None


def _subagent_text(value: Any, limit: int = 256) -> str | None:
    """Persist only bounded observer strings, not arbitrary object representations."""
    return value[:limit] if isinstance(value, str) and value else None


def _subagent_match_sql(alias: str, *, child_session_id: Any, child_subagent_id: Any,
                        parent_turn_id: Any) -> tuple[str | None, tuple[str, ...]]:
    child_session = _subagent_identity(child_session_id)
    child_subagent = _subagent_identity(child_subagent_id)
    turn_id = _subagent_identity(parent_turn_id)
    if child_session is not None:
        return f"COALESCE({alias}.child_session_id, {alias}.child_session_ref) = ?", (child_session,)
    if child_subagent is not None:
        predicate = f"{alias}.child_subagent_id = ?"
        values: tuple[str, ...] = (child_subagent,)
        if turn_id is not None:
            predicate += f" AND {alias}.parent_turn_id = ?"
            values += (turn_id,)
        return predicate, values
    return None, ()


def begin_subagent(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    parent_session_id: Any = None,
    child_session_id: Any = None,
    parent_turn_id: Any = None,
    parent_subagent_id: Any = None,
    child_subagent_id: Any = None,
    child_role: Any = None,
    child_goal: Any = None,
    started_at: Any = None,
    observer_schema_version: str = OBSERVER_SCHEMA_VERSION,
) -> str | None:
    """Persist a metadata-only subagent start; never accept child transcript content."""
    if conn.execute(f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)).fetchone() is None:
        return None
    child_ref = _subagent_identity(child_session_id)
    child_fk = None
    if child_ref is not None and conn.execute(
        "SELECT 1 FROM sessions WHERE id = ?", (child_ref,)
    ).fetchone() is not None:
        child_fk = child_ref
    started = _optional_float(started_at)
    if started is None:
        started = time.time()
    event_id = uuid.uuid4().hex
    conn.execute(
        f"INSERT INTO {_SUBAGENTS_TABLE} "
        "(id, session_id, parent_session_id, child_session_id, child_session_ref, parent_turn_id, "
        "parent_subagent_id, child_subagent_id, child_role, child_goal, started_at, status, "
        "observer_schema_version, capture_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, 'start_only')",
        (event_id, session_id, _subagent_identity(parent_session_id), child_fk, child_ref,
         _subagent_identity(parent_turn_id), _subagent_identity(parent_subagent_id),
         _subagent_identity(child_subagent_id), _subagent_text(child_role), _subagent_text(child_goal, 4000),
         started, str(observer_schema_version or "unknown")),
    )
    conn.commit()
    return event_id


def _subagent_completion_values(event: dict[str, Any], started_at: Any) -> tuple[Any, ...]:
    stopped = _optional_float(event.get("stopped_at"))
    if stopped is None:
        stopped = time.time()
    duration = _optional_float(event.get("duration_ms"))
    if duration is None and started_at is not None:
        duration = max(0.0, (stopped - float(started_at)) * 1000)
    return (
        stopped, duration, _subagent_status(event.get("child_status")),
        _subagent_text(event.get("child_role")), _subagent_text(event.get("child_summary"), 4000),
        str(event.get("telemetry_schema_version") or "unknown"),
    )


def complete_subagent(
    conn: sqlite3.Connection,
    event_id: str,
    event: dict[str, Any],
) -> bool:
    """Attach one stop callback to a unique exact child lifecycle start."""
    row = conn.execute(
        f"SELECT started_at FROM {_SUBAGENTS_TABLE} WHERE id = ? AND capture_state = 'start_only'",
        (event_id,),
    ).fetchone()
    if row is None:
        return False
    values = _subagent_completion_values(event, row["started_at"])
    cursor = conn.execute(
        f"UPDATE {_SUBAGENTS_TABLE} SET stopped_at = ?, duration_ms = ?, status = ?, "
        "child_role = COALESCE(?, child_role), child_summary = ?, observer_schema_version = ?, "
        "capture_state = 'paired' WHERE id = ? AND capture_state = 'start_only'",
        (*values, event_id),
    )
    conn.commit()
    return cursor.rowcount == 1


def complete_matching_subagent(
    conn: sqlite3.Connection,
    session_id: str,
    event: dict[str, Any],
) -> bool:
    """Pair only a single durable start with a safe supplied child identity."""
    predicate, values = _subagent_match_sql(
        "subagent", child_session_id=event.get("child_session_id"),
        child_subagent_id=event.get("child_subagent_id"), parent_turn_id=event.get("parent_turn_id"),
    )
    if predicate is None:
        return False
    rows = conn.execute(
        f"SELECT id, started_at, capture_state, parent_turn_id, child_subagent_id "
        f"FROM {_SUBAGENTS_TABLE} AS subagent "
        "WHERE session_id = ? AND (" + predicate + ") ORDER BY id LIMIT 2",
        (session_id, *values),
    ).fetchall()
    if len(rows) != 1 or rows[0]["capture_state"] != "start_only":
        return False
    candidate = rows[0]
    for key in ("parent_turn_id", "child_subagent_id"):
        supplied = _subagent_identity(event.get(key))
        stored = _subagent_identity(candidate[key])
        if supplied is not None and stored is not None and supplied != stored:
            return False
    return complete_subagent(conn, str(candidate["id"]), event)


def record_unmatched_subagent_stop(
    conn: sqlite3.Connection,
    session_id: str,
    event: dict[str, Any],
    *,
    parent_session_id: Any = None,
) -> str | None:
    """Keep a stop callback without a safely matched start as explicit partial evidence."""
    if conn.execute(f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)).fetchone() is None:
        return None
    stopped, duration, status, role, summary, version = _subagent_completion_values(event, None)
    child_ref = _subagent_identity(event.get("child_session_id"))
    child_fk = None
    if child_ref is not None and conn.execute(
        "SELECT 1 FROM sessions WHERE id = ?", (child_ref,)
    ).fetchone() is not None:
        child_fk = child_ref
    event_id = uuid.uuid4().hex
    conn.execute(
        f"INSERT INTO {_SUBAGENTS_TABLE} "
        "(id, session_id, parent_session_id, child_session_id, child_session_ref, parent_turn_id, "
        "parent_subagent_id, child_subagent_id, child_role, stopped_at, duration_ms, status, "
        "child_summary, observer_schema_version, capture_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stop_only')",
        (event_id, session_id, _subagent_identity(parent_session_id), child_fk, child_ref,
         _subagent_identity(event.get("parent_turn_id")), _subagent_identity(event.get("parent_subagent_id")),
         _subagent_identity(event.get("child_subagent_id")), role, stopped, duration, status,
         summary, version),
    )
    conn.commit()
    return event_id


def link_subagent_child_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Attach previously observed child references once their source row exists."""
    if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
        return
    conn.execute(
        f"UPDATE {_SUBAGENTS_TABLE} SET child_session_id = ? "
        "WHERE child_session_id IS NULL AND child_session_ref = ?",
        (session_id, session_id),
    )
    conn.commit()


def session_subagents(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Return related subagent lifecycle metadata without reading or copying child messages."""
    rows = conn.execute(
        f"SELECT id, session_id, parent_session_id, COALESCE(child_session_id, child_session_ref) "
        f"AS child_session_id, parent_turn_id, parent_subagent_id, child_subagent_id, child_role, "
        f"child_goal, started_at, stopped_at, duration_ms, status, child_summary, "
        f"observer_schema_version, capture_state, "
        "EXISTS (SELECT 1 FROM sessions child_source WHERE child_source.id = "
        "COALESCE(subagent.child_session_id, subagent.child_session_ref)) AS child_session_available, "
        "EXISTS (SELECT 1 FROM sessions parent_source WHERE parent_source.id = "
        "subagent.parent_session_id) AS parent_session_available "
        f"FROM {_SUBAGENTS_TABLE} AS subagent "
        "WHERE session_id = ? OR child_session_id = ? OR child_session_ref = ? "
        "ORDER BY COALESCE(started_at, stopped_at), id",
        (session_id, session_id, session_id),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["relationship"] = "parent" if item["session_id"] == session_id else "child"
        result.append(item)
    return result


def subagent_session_coverage(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """Report subagent support and lifecycle gaps independently from tool coverage."""
    captured = conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone()
    family = conn.execute(
        f"SELECT observer_schema_version, support_state FROM {_FAMILY_COVERAGE_TABLE} "
        "WHERE session_id = ? AND event_family = 'subagent'", (session_id,)
    ).fetchone()
    rows = conn.execute(
        f"SELECT parent_session_id, child_session_id, child_session_ref, child_subagent_id, status, capture_state "
        f"FROM {_SUBAGENTS_TABLE} WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    base = {
        "event_count": len(rows),
        "unmatched_starts": sum(row["capture_state"] == "start_only" for row in rows),
        "unmatched_stops": sum(row["capture_state"] == "stop_only" for row in rows),
        "unknown_status_events": sum(row["status"] == "unknown" for row in rows),
        "identity_gaps": sum(
            row["parent_session_id"] is None or not any(
                row[key] for key in ("child_session_id", "child_session_ref", "child_subagent_id")
            ) for row in rows
        ),
    }
    if not captured:
        return {**base, "support_state": "unknown", "coverage_state": "not_captured", "observer_schema_version": None}
    if not family:
        return {**base, "support_state": "unknown", "coverage_state": "unknown", "observer_schema_version": None}
    version, support = str(family["observer_schema_version"]), str(family["support_state"])
    if support != "supported" or version != OBSERVER_SCHEMA_VERSION:
        coverage = "unknown"
    else:
        coverage = "partial" if (
            base["unmatched_starts"] or base["unmatched_stops"] or
            base["identity_gaps"] or base["unknown_status_events"]
        ) else "observed_no_known_gaps"
    return {**base, "support_state": support, "coverage_state": coverage, "observer_schema_version": version}


def subagent_metrics(
    conn: sqlite3.Connection,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Aggregate subagent starts/stops only for sessions with compatible family support."""
    current = float(now if now is not None else time.time())
    predicate, params = _session_window(window, current, _reset_cutoff(conn), timezone_name=timezone_name)
    total_sessions = int(conn.execute(f"SELECT COUNT(*) FROM sessions s WHERE {predicate}", params).fetchone()[0])
    captured_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM {_SESSIONS_TABLE} o JOIN sessions s ON s.id = o.session_id WHERE {predicate}", params
    ).fetchone()[0])
    family_from = (
        f"FROM {_FAMILY_COVERAGE_TABLE} f JOIN sessions s ON s.id = f.session_id "
        f"WHERE {predicate} AND f.event_family = 'subagent'"
    )
    supported_filter = "AND f.support_state = 'supported' AND f.observer_schema_version = ?"
    supported_params = (*params, OBSERVER_SCHEMA_VERSION)
    supported = int(conn.execute(f"SELECT COUNT(*) {family_from} {supported_filter}", supported_params).fetchone()[0])
    unsupported = int(conn.execute(
        f"SELECT COUNT(*) {family_from} AND f.support_state = 'unsupported'", params
    ).fetchone()[0])
    observed_predicate = (
        f"NOT EXISTS (SELECT 1 FROM {_SUBAGENTS_TABLE} a WHERE a.session_id = f.session_id "
        "AND (a.capture_state != 'paired' OR a.status = 'unknown' OR a.parent_session_id IS NULL OR "
        "(a.child_session_id IS NULL AND COALESCE(a.child_session_ref, '') = '' "
        "AND COALESCE(a.child_subagent_id, '') = '')))"
    )
    observed = int(conn.execute(
        f"SELECT COUNT(*) {family_from} {supported_filter} AND {observed_predicate}", supported_params
    ).fetchone()[0])
    partial = int(conn.execute(
        f"SELECT COUNT(*) {family_from} {supported_filter} AND NOT ({observed_predicate})", supported_params
    ).fetchone()[0])
    identity_gaps = int(conn.execute(
        f"SELECT COUNT(*) FROM {_SUBAGENTS_TABLE} a "
        f"JOIN {_FAMILY_COVERAGE_TABLE} f ON f.session_id = a.session_id "
        f"JOIN sessions s ON s.id = a.session_id WHERE {predicate} AND f.event_family = 'subagent' "
        "AND f.support_state = 'supported' AND f.observer_schema_version = ? "
        "AND (a.parent_session_id IS NULL OR (a.child_session_id IS NULL "
        "AND COALESCE(a.child_session_ref, '') = '' AND COALESCE(a.child_subagent_id, '') = ''))",
        supported_params,
    ).fetchone()[0])
    events = {"total": 0, "succeeded": 0, "failed": 0, "interrupted": 0, "unknown": 0}
    rows = conn.execute(
        f"SELECT a.status, a.capture_state, COUNT(*) AS n FROM {_SUBAGENTS_TABLE} a "
        f"JOIN {_FAMILY_COVERAGE_TABLE} f ON f.session_id = a.session_id "
        f"JOIN sessions s ON s.id = a.session_id WHERE {predicate} AND f.event_family = 'subagent' "
        "AND f.support_state = 'supported' AND f.observer_schema_version = ? GROUP BY a.status, a.capture_state",
        supported_params,
    )
    unmatched_starts = unmatched_stops = 0
    for row in rows:
        count = int(row["n"])
        events["total"] += count
        if row["status"] in events:
            events[row["status"]] += count
        if row["capture_state"] == "start_only":
            unmatched_starts += count
        elif row["capture_state"] == "stop_only":
            unmatched_stops += count
    unknown_sessions = max(0, captured_sessions - supported - unsupported)
    return {
        "window": window, "events": events, "unmatched_starts": unmatched_starts,
        "unmatched_stops": unmatched_stops, "identity_gaps": identity_gaps,
        "total_sessions": total_sessions,
        "captured_sessions": captured_sessions, "supported_sessions": supported,
        "observed_sessions": observed, "partial_sessions": partial,
        "unknown_sessions": unknown_sessions, "unsupported_sessions": unsupported,
        "not_captured_sessions": max(0, total_sessions - captured_sessions),
    }


def _approval_outcome(choice: Any) -> str:
    value = _approval_choice(choice)
    if value in {"once", "session", "always", "smart_approve"}:
        return "allowed"
    if value in {"deny", "smart_deny"}:
        return "denied"
    if value in {"timeout", "timed_out", "timed-out"}:
        return "timed_out"
    if value in {"cancelled", "canceled"}:
        return "cancelled"
    return "unknown"


def _approval_choice(value: Any) -> str:
    text = value if isinstance(value, str) else None
    choice = text.strip().lower() if text else ""
    allowed = {
        "once", "session", "always", "deny", "timeout", "timed_out", "timed-out",
        "cancelled", "canceled", "smart_approve", "smart_deny", "notify_failed",
    }
    return choice if choice in allowed else "unknown"


def _approval_decider(value: Any) -> str | None:
    text = value if isinstance(value, str) else None
    return "aux_llm" if text and text.strip().lower() == "aux_llm" else None


def approval_surface(value: Any) -> str:
    """Collapse observer surfaces to a small allowlisted vocabulary."""
    text = value if isinstance(value, str) else ""
    if text in {"cli", "gateway", "smart", "vault-payment"}:
        return text
    if text.startswith("mcp-elicitation/"):
        return "mcp-elicitation"
    if text.startswith("mcp-trust/"):
        return "mcp-trust"
    return "unknown"


def _approval_pattern_keys(_value: Any) -> str:
    """Pattern labels may be descriptions; deliberately do not persist them or fingerprints."""
    return "[]"


def begin_approval_request(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    session_key: Any = None,
    turn_id: Any = None,
    tool_call_id: Any = None,
    surface: Any = None,
    pattern_key: Any = None,
    pattern_keys: Any = None,
    requested_at: float | None = None,
    observer_schema_version: str = OBSERVER_SCHEMA_VERSION,
) -> str | None:
    """Record an approval request without retaining command or descriptive text."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return None
    event_id = uuid.uuid4().hex
    conn.execute(
        f"INSERT INTO {_APPROVALS_TABLE} "
        "(id, session_id, session_key, turn_id, tool_call_id, surface, pattern_key, pattern_keys, "
        "requested_at, outcome, observer_schema_version, capture_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, 'request_only')",
        (event_id, session_id, _identity_text(session_key), _identity_text(turn_id),
         _identity_text(tool_call_id), approval_surface(surface), None,
         _approval_pattern_keys(pattern_keys), float(requested_at if requested_at is not None else time.time()),
         str(observer_schema_version or "unknown")),
    )
    conn.commit()
    return event_id


def approval_key_seen(conn: sqlite3.Connection, key: tuple[str, ...]) -> bool:
    """Check durable approval evidence before reusing an opaque correlation key."""
    if len(key) != 4:
        return False
    session_id, surface, turn_id, tool_call_id = key
    return conn.execute(
        f"SELECT 1 FROM {_APPROVALS_TABLE} WHERE session_id = ? "
        "AND COALESCE(surface, '') = ? AND COALESCE(turn_id, '') = ? "
        "AND COALESCE(tool_call_id, '') = ? LIMIT 1",
        (session_id, surface, turn_id, tool_call_id),
    ).fetchone() is not None


def complete_approval_request(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    choice: Any,
    decided_by: Any = None,
    decided_at: float | None = None,
    observer_schema_version: str = OBSERVER_SCHEMA_VERSION,
) -> bool:
    """Attach an explicit approval decision to exactly one observed request."""
    decision_time = float(decided_at if decided_at is not None else time.time())
    row = conn.execute(
        f"SELECT requested_at FROM {_APPROVALS_TABLE} WHERE id = ? AND capture_state = 'request_only'",
        (event_id,),
    ).fetchone()
    if row is None:
        return False
    requested_at = row["requested_at"]
    duration = max(0.0, (decision_time - float(requested_at)) * 1000) if requested_at is not None else None
    cursor = conn.execute(
        f"UPDATE {_APPROVALS_TABLE} SET decided_at = ?, duration_ms = ?, outcome = ?, choice = ?, "
        "decided_by = ?, observer_schema_version = ?, capture_state = 'paired' "
        "WHERE id = ? AND capture_state = 'request_only'",
        (decision_time, duration, _approval_outcome(choice), _approval_choice(choice),
         _approval_decider(decided_by), str(observer_schema_version or "unknown"), event_id),
    )
    conn.commit()
    return cursor.rowcount == 1


def record_post_only_approval(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    session_key: Any = None,
    turn_id: Any = None,
    tool_call_id: Any = None,
    surface: Any = None,
    pattern_key: Any = None,
    pattern_keys: Any = None,
    choice: Any = None,
    decided_by: Any = None,
    decided_at: float | None = None,
    observer_schema_version: str = OBSERVER_SCHEMA_VERSION,
) -> str | None:
    """Retain a decision without a safe matching request as partial evidence."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return None
    event_id = uuid.uuid4().hex
    conn.execute(
        f"INSERT INTO {_APPROVALS_TABLE} "
        "(id, session_id, session_key, turn_id, tool_call_id, surface, pattern_key, pattern_keys, "
        "decided_at, outcome, choice, decided_by, observer_schema_version, capture_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'response_only')",
        (event_id, session_id, _identity_text(session_key), _identity_text(turn_id),
         _identity_text(tool_call_id), approval_surface(surface), None,
         _approval_pattern_keys(pattern_keys), float(decided_at if decided_at is not None else time.time()),
         _approval_outcome(choice), _approval_choice(choice), _approval_decider(decided_by),
         str(observer_schema_version or "unknown")),
    )
    conn.commit()
    return event_id


def session_approvals(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Return bounded approval lifecycle metadata; sensitive command text is never stored."""
    rows = conn.execute(
        f"SELECT id, session_id, session_key, turn_id, tool_call_id, surface, pattern_key, pattern_keys, "
        "requested_at, decided_at, duration_ms, outcome, choice, decided_by, observer_schema_version, capture_state "
        f"FROM {_APPROVALS_TABLE} WHERE session_id = ? "
        "ORDER BY COALESCE(requested_at, decided_at), id",
        (session_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["pattern_keys"] = json.loads(item["pattern_keys"])
        except (TypeError, json.JSONDecodeError):
            item["pattern_keys"] = []
        item["related_call_id"] = None
        item["related_call_status"] = "unavailable"
        if item["tool_call_id"]:
            related = conn.execute(
                f"SELECT id FROM {_CALLS_TABLE} WHERE session_id = ? AND tool_call_id = ? "
                "AND (? IS NULL OR turn_id = ?) LIMIT 2",
                (session_id, item["tool_call_id"], item["turn_id"], item["turn_id"]),
            ).fetchall()
            if len(related) == 1:
                item["related_call_id"] = str(related[0]["id"])
                item["related_call_status"] = "matched"
            elif related:
                item["related_call_status"] = "ambiguous"
            else:
                item["related_call_status"] = "not_found"
        result.append(item)
    return result


def _safe_text(value: Any, limit: int = 256) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value)
    return text[:limit] if text else None


def _identity_text(value: Any) -> str | None:
    """Preserve opaque observer identifiers verbatim; never trim or truncate them."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value)
    return text if text else None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_int(value: Any, *, minimum: int = 0, maximum: int = 2**31 - 1) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if minimum <= number <= maximum else None


def _usage_json(usage: Any) -> str:
    """Keep only known numeric token counters; never serialize provider objects."""
    allowed = {
        "input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
    }
    safe: dict[str, int] = {}
    if isinstance(usage, dict):
        for key in allowed:
            value = _optional_int(usage.get(key))
            if value is not None:
                safe[key] = value
    return json.dumps(safe, sort_keys=True, separators=(",", ":"))


def begin_provider_attempt(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    task_id: Any = None,
    turn_id: Any = None,
    api_request_id: Any = None,
    api_call_count: Any = None,
    provider: Any = None,
    model: Any = None,
    started_at: Any = None,
    observer_schema_version: str = OBSERVER_SCHEMA_VERSION,
) -> str | None:
    """Persist a metadata-only provider attempt; raw request payloads are never accepted."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return None
    attempt_id = uuid.uuid4().hex
    start = _optional_float(started_at)
    if start is None:
        start = time.time()
    conn.execute(
        f"INSERT INTO {_PROVIDER_ATTEMPTS_TABLE} "
        "(id, session_id, task_id, turn_id, api_request_id, api_call_count, provider, model, "
        "started_at, observer_schema_version, outcome, capture_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 'start_only')",
        (attempt_id, session_id, _identity_text(task_id), _identity_text(turn_id), _identity_text(api_request_id),
         _optional_int(api_call_count, minimum=1), _safe_text(provider), _safe_text(model), start,
         str(observer_schema_version or "unknown")),
    )
    conn.commit()
    return attempt_id


def _provider_update_values(event: dict[str, Any], outcome: str) -> tuple[Any, ...]:
    ended = _optional_float(event.get("ended_at"))
    if ended is None:
        ended = time.time()
    duration = _optional_float(event.get("api_duration"))
    retryable = event.get("retryable")
    if not isinstance(retryable, bool):
        retryable = None
    error = event.get("error")
    if not isinstance(error, dict):
        error = {}
    return (
        ended, duration, outcome,
        _optional_int(event.get("status_code"), maximum=999),
        _optional_int(event.get("retry_count")), _optional_int(event.get("max_retries")),
        None if retryable is None else int(retryable), _safe_text(error.get("type")),
        _safe_text(event.get("response_model")), _safe_text(event.get("finish_reason")),
        _usage_json(event.get("usage")),
    )


def complete_provider_attempt(
    conn: sqlite3.Connection, attempt_id: str, event: dict[str, Any], *, outcome: str
) -> bool:
    """Attach one success/error callback to its exact opaque API request ID."""
    if outcome not in {"ok", "failed", "unknown"}:
        outcome = "unknown"
    cursor = conn.execute(
        f"UPDATE {_PROVIDER_ATTEMPTS_TABLE} SET ended_at = ?, api_duration = ?, outcome = ?, "
        "status_code = ?, retry_count = ?, max_retries = ?, retryable = ?, error_type = ?, "
        "response_model = ?, finish_reason = ?, usage = ?, capture_state = 'paired' "
        "WHERE id = ? AND ended_at IS NULL",
        (*_provider_update_values(event, outcome), attempt_id),
    )
    conn.commit()
    return cursor.rowcount == 1


def record_post_only_provider_attempt(
    conn: sqlite3.Connection,
    session_id: str,
    event: dict[str, Any],
    *,
    outcome: str,
    observer_schema_version: str = OBSERVER_SCHEMA_VERSION,
) -> str | None:
    """Keep an unpaired API outcome visible while making family coverage partial."""
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return None
    if outcome not in {"ok", "failed", "unknown"}:
        outcome = "unknown"
    attempt_id = uuid.uuid4().hex
    values = _provider_update_values(event, outcome)
    conn.execute(
        f"INSERT INTO {_PROVIDER_ATTEMPTS_TABLE} "
        "(id, session_id, task_id, turn_id, api_request_id, api_call_count, provider, model, "
        "ended_at, api_duration, outcome, status_code, retry_count, max_retries, retryable, error_type, "
        "response_model, finish_reason, usage, observer_schema_version, capture_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'post_only')",
        (attempt_id, session_id, _identity_text(event.get("task_id")), _identity_text(event.get("turn_id")),
         _identity_text(event.get("api_request_id")), _optional_int(event.get("api_call_count"), minimum=1),
         _safe_text(event.get("provider")), _safe_text(event.get("model")), *values,
         str(observer_schema_version or "unknown")),
    )
    conn.commit()
    return attempt_id


def session_provider_attempts(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Return safe provider attempt metadata for one source session."""
    rows = conn.execute(
        f"SELECT id, session_id, task_id, turn_id, api_request_id, api_call_count, provider, model, "
        "started_at, ended_at, api_duration, outcome, status_code, retry_count, max_retries, retryable, "
        "error_type, response_model, finish_reason, usage, observer_schema_version, capture_state "
        f"FROM {_PROVIDER_ATTEMPTS_TABLE} WHERE session_id = ? "
        "ORDER BY COALESCE(started_at, ended_at), id",
        (session_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["usage"] = json.loads(item["usage"])
        except (TypeError, json.JSONDecodeError):
            item["usage"] = {}
        result.append(item)
    return result


def approval_session_coverage(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """Describe approval-family support and lifecycle gaps separately from tool calls."""
    captured = conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone()
    family = conn.execute(
        f"SELECT observer_schema_version, support_state FROM {_FAMILY_COVERAGE_TABLE} "
        "WHERE session_id = ? AND event_family = 'approval'",
        (session_id,),
    ).fetchone()
    request_count = int(conn.execute(
        f"SELECT COUNT(*) FROM {_APPROVALS_TABLE} WHERE session_id = ? AND capture_state != 'response_only'",
        (session_id,),
    ).fetchone()[0])
    unpaired_response_count = int(conn.execute(
        f"SELECT COUNT(*) FROM {_APPROVALS_TABLE} WHERE session_id = ? AND capture_state = 'response_only'",
        (session_id,),
    ).fetchone()[0])
    base = {"request_count": request_count, "unpaired_response_count": unpaired_response_count}
    if not captured:
        return {
            **base, "support_state": "unknown", "coverage_state": "not_captured",
            "observer_schema_version": None,
        }
    if not family:
        return {
            **base, "support_state": "unknown", "coverage_state": "unknown",
            "observer_schema_version": None,
        }
    version = str(family["observer_schema_version"])
    support = str(family["support_state"])
    if support != "supported" or version != OBSERVER_SCHEMA_VERSION:
        coverage = "unknown"
    else:
        gap = conn.execute(
            f"SELECT 1 FROM {_APPROVALS_TABLE} WHERE session_id = ? "
            "AND (capture_state != 'paired' OR outcome = 'unknown') LIMIT 1",
            (session_id,),
        ).fetchone()
        coverage = "partial" if gap else "observed_no_known_gaps"
    return {
        **base, "support_state": support, "coverage_state": coverage,
        "observer_schema_version": version,
    }


def approval_metrics(
    conn: sqlite3.Connection,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Aggregate explicit approval evidence with its own support and gap counts."""
    current = float(now if now is not None else time.time())
    predicate, params = _session_window(
        window, current, _reset_cutoff(conn), timezone_name=timezone_name
    )
    total_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM sessions s WHERE {predicate}", params
    ).fetchone()[0])
    captured_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM {_SESSIONS_TABLE} o JOIN sessions s ON s.id = o.session_id "
        f"WHERE {predicate}", params
    ).fetchone()[0])
    family_from = (
        f"FROM {_FAMILY_COVERAGE_TABLE} f JOIN sessions s ON s.id = f.session_id "
        f"WHERE {predicate} AND f.event_family = 'approval'"
    )
    family_params = (*params, OBSERVER_SCHEMA_VERSION)
    supported_filter = "AND f.support_state = 'supported' AND f.observer_schema_version = ?"
    supported = int(conn.execute(
        f"SELECT COUNT(*) {family_from} {supported_filter}", family_params
    ).fetchone()[0])
    unsupported = int(conn.execute(
        f"SELECT COUNT(*) {family_from} AND f.support_state = 'unsupported'", params
    ).fetchone()[0])
    unknown = max(0, captured_sessions - supported - unsupported)
    gap_predicate = (
        f"NOT EXISTS (SELECT 1 FROM {_APPROVALS_TABLE} a "
        "WHERE a.session_id = f.session_id AND (a.capture_state != 'paired' OR a.outcome = 'unknown'))"
    )
    observed = int(conn.execute(
        f"SELECT COUNT(*) {family_from} {supported_filter} AND {gap_predicate}", family_params
    ).fetchone()[0])
    partial = int(conn.execute(
        f"SELECT COUNT(*) {family_from} {supported_filter} AND NOT ({gap_predicate})", family_params
    ).fetchone()[0])
    events = {"requests": 0, "allowed": 0, "denied": 0, "timed_out": 0, "cancelled": 0, "unknown": 0}
    for row in conn.execute(
        f"SELECT a.outcome, COUNT(*) AS n FROM {_APPROVALS_TABLE} a "
        f"JOIN {_FAMILY_COVERAGE_TABLE} f ON f.session_id = a.session_id "
        f"JOIN sessions s ON s.id = a.session_id WHERE {predicate} "
        "AND f.event_family = 'approval' AND f.support_state = 'supported' "
        "AND f.observer_schema_version = ? AND a.capture_state != 'response_only' "
        "GROUP BY a.outcome",
        (*params, OBSERVER_SCHEMA_VERSION),
    ):
        outcome = str(row["outcome"])
        count = int(row["n"])
        events["requests"] += count
        if outcome in events:
            events[outcome] += count
    unpaired_responses = int(conn.execute(
        f"SELECT COUNT(*) FROM {_APPROVALS_TABLE} a "
        f"JOIN {_FAMILY_COVERAGE_TABLE} f ON f.session_id = a.session_id "
        f"JOIN sessions s ON s.id = a.session_id WHERE {predicate} "
        "AND f.event_family = 'approval' AND f.support_state = 'supported' "
        "AND f.observer_schema_version = ? AND a.capture_state = 'response_only'",
        (*params, OBSERVER_SCHEMA_VERSION),
    ).fetchone()[0])
    return {
        "window": window,
        "events": events,
        "unpaired_responses": unpaired_responses,
        "total_sessions": total_sessions,
        "captured_sessions": captured_sessions,
        "supported_sessions": supported,
        "observed_sessions": observed,
        "partial_sessions": partial,
        "unknown_sessions": unknown,
        "unsupported_sessions": unsupported,
        "not_captured_sessions": max(0, total_sessions - captured_sessions),
    }


def provider_session_coverage(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """Describe provider-family support and detected gaps for one eligible session."""
    captured = conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone()
    family = conn.execute(
        f"SELECT observer_schema_version, support_state FROM {_FAMILY_COVERAGE_TABLE} "
        "WHERE session_id = ? AND event_family = 'provider_api'",
        (session_id,),
    ).fetchone()
    attempt_count = int(conn.execute(
        f"SELECT COUNT(*) FROM {_PROVIDER_ATTEMPTS_TABLE} WHERE session_id = ? "
        "AND capture_state != 'post_only'", (session_id,)
    ).fetchone()[0])
    unpaired_event_count = int(conn.execute(
        f"SELECT COUNT(*) FROM {_PROVIDER_ATTEMPTS_TABLE} WHERE session_id = ? "
        "AND capture_state = 'post_only'", (session_id,)
    ).fetchone()[0])
    if not captured:
        return {
            "support_state": "unknown", "coverage_state": "not_captured",
            "observer_schema_version": None, "attempt_count": attempt_count,
            "unpaired_event_count": unpaired_event_count,
        }
    if not family:
        return {
            "support_state": "unknown", "coverage_state": "unknown",
            "observer_schema_version": None, "attempt_count": attempt_count,
            "unpaired_event_count": unpaired_event_count,
        }
    version = str(family["observer_schema_version"])
    support = str(family["support_state"])
    if support != "supported" or version != OBSERVER_SCHEMA_VERSION:
        coverage = "unknown"
    else:
        gap = conn.execute(
            f"SELECT 1 FROM {_PROVIDER_ATTEMPTS_TABLE} WHERE session_id = ? "
            "AND (capture_state != 'paired' OR outcome = 'unknown') LIMIT 1",
            (session_id,),
        ).fetchone()
        coverage = "partial" if gap else "observed_no_known_gaps"
    return {
        "support_state": support, "coverage_state": coverage,
        "observer_schema_version": version, "attempt_count": attempt_count,
        "unpaired_event_count": unpaired_event_count,
    }


def provider_metrics(
    conn: sqlite3.Connection,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Aggregate API attempts separately with family-specific support and gap counts."""
    current = float(now if now is not None else time.time())
    predicate, params = _session_window(
        window, current, _reset_cutoff(conn), timezone_name=timezone_name
    )
    counts = {"total": 0, "ok": 0, "failed": 0, "unknown": 0}
    for row in conn.execute(
        f"SELECT p.outcome, COUNT(*) AS n FROM {_PROVIDER_ATTEMPTS_TABLE} p "
        f"JOIN sessions s ON s.id = p.session_id WHERE {predicate} "
        "AND p.capture_state != 'post_only' GROUP BY p.outcome",
        params,
    ):
        outcome = str(row["outcome"])
        if outcome in counts:
            counts[outcome] = int(row["n"])
            counts["total"] += int(row["n"])
    total_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM sessions s WHERE {predicate}", params
    ).fetchone()[0])
    family_from = (
        f"FROM {_FAMILY_COVERAGE_TABLE} f JOIN sessions s ON s.id = f.session_id "
        f"WHERE {predicate} AND f.event_family = 'provider_api'"
    )
    eligible_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM {_SESSIONS_TABLE} o JOIN sessions s ON s.id = o.session_id "
        f"WHERE {predicate}", params
    ).fetchone()[0])
    supported_filter = "AND f.support_state = 'supported' AND f.observer_schema_version = ?"
    supported = int(conn.execute(
        f"SELECT COUNT(*) {family_from} {supported_filter}",
        (*params, OBSERVER_SCHEMA_VERSION),
    ).fetchone()[0])
    unsupported = int(conn.execute(
        f"SELECT COUNT(*) {family_from} AND f.support_state = 'unsupported'",
        params,
    ).fetchone()[0])
    unknown = max(0, eligible_sessions - supported - unsupported)
    gap_predicate = (
        f"NOT EXISTS (SELECT 1 FROM {_PROVIDER_ATTEMPTS_TABLE} p "
        "WHERE p.session_id = f.session_id AND (p.capture_state != 'paired' OR p.outcome = 'unknown'))"
    )
    observed = int(conn.execute(
        f"SELECT COUNT(*) {family_from} {supported_filter} AND {gap_predicate}",
        (*params, OBSERVER_SCHEMA_VERSION),
    ).fetchone()[0])
    partial = int(conn.execute(
        f"SELECT COUNT(*) {family_from} {supported_filter} AND NOT ({gap_predicate})",
        (*params, OBSERVER_SCHEMA_VERSION),
    ).fetchone()[0])
    observed_attempts = {"ok": 0, "failed": 0, "denominator": 0}
    for row in conn.execute(
        f"SELECT p.outcome, COUNT(*) AS n FROM {_PROVIDER_ATTEMPTS_TABLE} p "
        f"JOIN {_FAMILY_COVERAGE_TABLE} f ON f.session_id = p.session_id "
        "JOIN sessions s ON s.id = p.session_id "
        f"WHERE {predicate} AND f.event_family = 'provider_api' "
        "AND f.support_state = 'supported' AND f.observer_schema_version = ? "
        f"AND {gap_predicate} AND p.outcome IN ('ok', 'failed') GROUP BY p.outcome",
        (*params, OBSERVER_SCHEMA_VERSION),
    ):
        observed_attempts[str(row["outcome"])] = int(row["n"])
    observed_attempts["denominator"] = observed_attempts["ok"] + observed_attempts["failed"]
    rate = observed_attempts["ok"] / observed_attempts["denominator"] if observed_attempts["denominator"] else None
    unpaired_events = int(conn.execute(
        f"SELECT COUNT(*) FROM {_PROVIDER_ATTEMPTS_TABLE} p "
        f"JOIN sessions s ON s.id = p.session_id WHERE {predicate} AND p.capture_state = 'post_only'",
        params,
    ).fetchone()[0])
    retryable_failures = int(conn.execute(
        f"SELECT COUNT(*) FROM {_PROVIDER_ATTEMPTS_TABLE} p "
        f"JOIN sessions s ON s.id = p.session_id WHERE {predicate} "
        "AND p.capture_state != 'post_only' AND p.retryable = 1",
        params,
    ).fetchone()[0])
    return {
        "window": window, "attempts": counts, "unpaired_events": unpaired_events,
        "retryable_failures": retryable_failures,
        "success_rate": rate, "total_sessions": total_sessions,
        "captured_sessions": eligible_sessions, "supported_sessions": supported,
        "observed_sessions": observed, "partial_sessions": partial,
        "unknown_sessions": unknown, "unsupported_sessions": unsupported,
        "not_captured_sessions": max(0, total_sessions - eligible_sessions),
        "rate_excluded_sessions": max(0, total_sessions - observed),
        "rate_attempts": observed_attempts,
    }


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
    if value in {"error", "failed", "failure", "timeout", "timed_out", "timed-out",
                 "partial_failure", "partial-failure", "partial failure"}:
        return "failed"
    if value in {"blocked", "cancelled", "canceled"}:
        return "not_executed"
    return "unknown"


def _bounded(value: Any, limit: int = 2000) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


def _failure_status(status: Any) -> str | None:
    if normalize_outcome(status) != "failed" or status is None:
        return None
    return _bounded(str(status).strip().lower(), 64)


def complete_tool_call(
    conn: sqlite3.Connection,
    call_id: str,
    status: Any,
    *,
    completed_at: float | None = None,
    duration_ms: float | None = None,
    error_type: Any = None,
    error_code: Any = None,
    error_message: Any = None,
) -> bool:
    """Close one started invocation using only the explicit observer outcome."""
    cursor = conn.execute(
        f"UPDATE {_CALLS_TABLE} SET completed_at = ?, duration_ms = ?, outcome = ?, "
        "error_type = ?, error_code = ?, error_message = ?, failure_status = ?, capture_state = 'paired' "
        "WHERE id = ? AND completed_at IS NULL",
        (float(completed_at if completed_at is not None else time.time()), duration_ms,
         normalize_outcome(status), _bounded(error_type, 256), _bounded(error_code, 256),
         _bounded(error_message), _failure_status(status), call_id),
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
    error_code: Any = None,
    error_message: Any = None,
) -> str | None:
    """Record a post hook that had no matching start as visible, partial evidence."""
    normalized_tool_name = str(tool_name or "unknown")
    if conn.execute(
        f"SELECT 1 FROM {_SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone() is None:
        return None
    if tool_call_id:
        existing = conn.execute(
            f"SELECT id FROM {_CALLS_TABLE} WHERE session_id = ? AND tool_name = ? AND tool_call_id = ? "
            "AND COALESCE(turn_id, '') = COALESCE(?, '') "
            "AND COALESCE(api_request_id, '') = COALESCE(?, '') "
            "AND completed_at IS NOT NULL LIMIT 1",
            (session_id, normalized_tool_name, tool_call_id, turn_id, api_request_id),
        ).fetchone()
        if existing:
            return str(existing[0])
    call_id = uuid.uuid4().hex
    conn.execute(
        f"INSERT INTO {_CALLS_TABLE} "
        "(id, session_id, tool_name, task_id, turn_id, api_request_id, tool_call_id, "
        "completed_at, duration_ms, outcome, error_type, error_code, error_message, failure_status, capture_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'post_only')",
        (call_id, session_id, normalized_tool_name, task_id, turn_id, api_request_id,
         tool_call_id, float(completed_at if completed_at is not None else time.time()), duration_ms,
         normalize_outcome(status), _bounded(error_type, 256), _bounded(error_code, 256),
         _bounded(error_message), _failure_status(status)),
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
        "started_at, completed_at, duration_ms, outcome, error_type, error_code, error_message, capture_state "
        f"FROM {_CALLS_TABLE} WHERE session_id = ? ORDER BY COALESCE(started_at, completed_at), id",
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def tool_breakdown(
    conn: sqlite3.Connection,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate exact tool identities with per-tool compatible coverage."""
    current = float(now if now is not None else time.time())
    predicate, params = _session_window(
        window, current, _reset_cutoff(conn), timezone_name=timezone_name
    )
    sessions = conn.execute(
        f"SELECT s.id, o.observer_schema_version FROM sessions s "
        f"LEFT JOIN {_SESSIONS_TABLE} o ON o.session_id = s.id WHERE {predicate}",
        params,
    ).fetchall()
    total_sessions = len(sessions)
    captured_sessions = {
        str(row["id"]): row["observer_schema_version"] for row in sessions
        if row["observer_schema_version"] is not None
    }
    call_rows = conn.execute(
        f"SELECT c.session_id, c.tool_name, c.outcome, c.capture_state FROM {_CALLS_TABLE} c "
        f"JOIN sessions s ON s.id = c.session_id WHERE {predicate} ORDER BY c.tool_name",
        params,
    ).fetchall()
    compatible_ids = {
        session_id for session_id, version in captured_sessions.items()
        if version == OBSERVER_SCHEMA_VERSION
    }
    unknown_sessions = len(captured_sessions) - len(compatible_ids)
    not_captured_sessions = total_sessions - len(captured_sessions)
    identity_gap_sessions: set[str] = set()
    by_tool: dict[str, dict[str, Any]] = {}
    for row in call_rows:
        tool_name = str(row["tool_name"])
        session_id = str(row["session_id"])
        if not tool_name or tool_name == "unknown":
            identity_gap_sessions.add(session_id)
            continue
        item = by_tool.setdefault(tool_name, {
            "calls": {"total": 0, "ok": 0, "failed": 0, "not_executed": 0, "unknown": 0},
            "rows": [],
            "gap_sessions": set(),
        })
        item["rows"].append(row)
        outcome = str(row["outcome"])
        if outcome in item["calls"]:
            item["calls"][outcome] += 1
            item["calls"]["total"] += 1
        if (session_id in compatible_ids
                and (row["capture_state"] != "paired" or outcome == "unknown")):
            item["gap_sessions"].add(session_id)

    results: list[dict[str, Any]] = []
    for tool_name, item in by_tool.items():
        gap_sessions = item["gap_sessions"] | (identity_gap_sessions & compatible_ids)
        observed_sessions = len(compatible_ids) - len(gap_sessions)
        rate_calls = {"ok": 0, "failed": 0, "denominator": 0}
        for row in item["rows"]:
            if (str(row["session_id"]) in compatible_ids
                    and str(row["session_id"]) not in gap_sessions
                    and row["outcome"] in ("ok", "failed")):
                rate_calls[str(row["outcome"])] += 1
        rate_calls["denominator"] = rate_calls["ok"] + rate_calls["failed"]
        results.append({
            "tool_name": tool_name,
            "calls": item["calls"],
            "rate_calls": rate_calls,
            "success_rate": (
                rate_calls["ok"] / rate_calls["denominator"]
                if rate_calls["denominator"] else None
            ),
            "coverage": {
                "total_sessions": total_sessions,
                "observed_sessions": observed_sessions,
                "partial_sessions": len(gap_sessions),
                "unknown_sessions": unknown_sessions,
                "not_captured_sessions": not_captured_sessions,
            },
        })
    return sorted(results, key=lambda item: (-item["calls"]["total"], item["tool_name"]))


def call_record(conn: sqlite3.Connection, session_id: str, call_id: str) -> dict[str, Any] | None:
    """Return one captured call's metadata, scoped to its exact source session."""
    row = conn.execute(
        f"SELECT id, session_id, tool_name, tool_call_id, started_at, completed_at, "
        f"duration_ms, outcome, error_type, error_code, error_message FROM {_CALLS_TABLE} "
        "WHERE session_id = ? AND id = ?",
        (session_id, call_id),
    ).fetchone()
    return dict(row) if row else None


def reset_metrics(conn: sqlite3.Connection, *, reset_at: float | None = None) -> float:
    """Advance the Since Reset cutoff without changing sessions or telemetry rows."""
    candidate = float(reset_at if reset_at is not None else time.time())
    previous = _reset_cutoff(conn)
    cutoff = max(candidate, previous + 0.000001) if previous is not None else candidate
    conn.execute(
        f"INSERT INTO {_META_TABLE}(key, value) VALUES ('reset_cutoff', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (repr(cutoff),),
    )
    conn.commit()
    return cutoff


def _reset_cutoff(conn: sqlite3.Connection) -> float | None:
    row = conn.execute("SELECT value FROM hermes_observability_meta WHERE key = 'reset_cutoff'").fetchone()
    return float(row[0]) if row else None


def _today_bounds(now: float, timezone_name: str | None = None) -> tuple[float, float]:
    """Return local calendar-day bounds, including correct DST transitions."""
    if timezone_name:
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("invalid IANA timezone") from exc
        day = datetime.fromtimestamp(now, zone).date()
        next_day = day + timedelta(days=1)
        start = datetime.combine(day, datetime_time.min, tzinfo=zone).timestamp()
        end = datetime.combine(next_day, datetime_time.min, tzinfo=zone).timestamp()
        return start, end
    local = time.localtime(now)
    day = date(local.tm_year, local.tm_mon, local.tm_mday)
    next_day = day + timedelta(days=1)
    start = time.mktime((day.year, day.month, day.day, 0, 0, 0, 0, 0, -1))
    end = time.mktime((next_day.year, next_day.month, next_day.day, 0, 0, 0, 0, 0, -1))
    return start, end


def _session_window(
    window: str,
    now: float,
    reset_cutoff: float | None,
    alias: str = "s",
    timezone_name: str | None = None,
) -> tuple[str, tuple[float, ...]]:
    if window == "Overall":
        return "1 = 1", ()
    if window == "Today":
        start, end = _today_bounds(now, timezone_name)
        return f"{alias}.started_at >= ? AND {alias}.started_at < ?", (start, end)
    if window == "Since Reset":
        if reset_cutoff is None:
            return "1 = 1", ()
        return f"{alias}.started_at >= ?", (reset_cutoff,)
    raise ValueError("window must be Overall, Today, or Since Reset")


def list_sessions(
    conn: sqlite3.Connection,
    limit: int = 100,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> list[dict[str, Any]]:
    """List session headers and explicit coverage labels, newest first."""
    limit = max(1, min(int(limit), 500))
    current = float(now if now is not None else time.time())
    predicate, params = _session_window(
        window, current, _reset_cutoff(conn), timezone_name=timezone_name
    )
    rows = conn.execute(
        f"SELECT s.id, s.title, s.started_at, s.ended_at, o.captured_at, "
        f"o.observer_schema_version FROM sessions s LEFT JOIN {_SESSIONS_TABLE} o "
        f"ON o.session_id = s.id WHERE {predicate} ORDER BY s.started_at DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    call_counts_by_session: dict[str, dict[str, int]] = {
        str(row["id"]): {"total": 0, "ok": 0, "failed": 0, "not_executed": 0, "unknown": 0}
        for row in rows
    }
    if call_counts_by_session:
        placeholders = ",".join("?" for _ in call_counts_by_session)
        for call_row in conn.execute(
            f"SELECT session_id, outcome, COUNT(*) AS n FROM {_CALLS_TABLE} "
            f"WHERE session_id IN ({placeholders}) GROUP BY session_id, outcome",
            tuple(call_counts_by_session),
        ):
            counts = call_counts_by_session[str(call_row["session_id"])]
            outcome = str(call_row["outcome"])
            if outcome in counts:
                counts[outcome] = int(call_row["n"])
                counts["total"] += int(call_row["n"])

    result = []
    for row in rows:
        item = dict(row)
        version = item.pop("observer_schema_version")
        item["coverage_state"] = (
            "not_captured" if item["captured_at"] is None
            else _coverage_state(conn, item["id"], version)
        )
        call_counts = call_counts_by_session[str(item["id"])]
        item["call_counts"] = call_counts
        item["call_count"] = call_counts["total"]
        denominator = call_counts["ok"] + call_counts["failed"]
        item["call_success_rate"] = (
            call_counts["ok"] / denominator
            if item["coverage_state"] == "observed_no_known_gaps" and denominator else None
        )
        result.append(item)
    return result


def session_header(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any] | None:
    """Fetch one exact session header without the recent-session list limit."""
    current = float(now if now is not None else time.time())
    predicate, params = _session_window(
        window, current, _reset_cutoff(conn), timezone_name=timezone_name
    )
    row = conn.execute(
        f"SELECT s.id, s.title, s.started_at, s.ended_at, o.captured_at, "
        f"o.observer_schema_version FROM sessions s LEFT JOIN {_SESSIONS_TABLE} o "
        f"ON o.session_id = s.id WHERE s.id = ? AND ({predicate}) LIMIT 1",
        (session_id, *params),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    version = item.pop("observer_schema_version")
    item["coverage_state"] = (
        "not_captured" if item["captured_at"] is None
        else _coverage_state(conn, item["id"], version)
    )
    item["call_count"] = conn.execute(
        f"SELECT COUNT(*) FROM {_CALLS_TABLE} WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    return item


def metrics(
    conn: sqlite3.Connection,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Aggregate counts and coverage within one whole-session time window."""
    current = float(now if now is not None else time.time())
    reset_cutoff = _reset_cutoff(conn)
    predicate, params = _session_window(
        window, current, reset_cutoff, timezone_name=timezone_name
    )
    counts = {"total": 0, "ok": 0, "failed": 0, "not_executed": 0, "unknown": 0}
    for row in conn.execute(
        f"SELECT c.outcome, COUNT(*) AS n FROM {_CALLS_TABLE} c "
        f"JOIN sessions s ON s.id = c.session_id WHERE {predicate} GROUP BY c.outcome",
        params,
    ):
        key = row["outcome"]
        if key in counts:
            counts[key] = int(row["n"])
            counts["total"] += int(row["n"])

    rate_counts = {"ok": 0, "failed": 0, "denominator": 0}
    compatible = (
        f"SELECT o.session_id FROM {_SESSIONS_TABLE} o "
        "JOIN sessions s ON s.id = o.session_id "
        f"WHERE {predicate} AND o.observer_schema_version = ? AND NOT EXISTS ("
        f"SELECT 1 FROM {_CALLS_TABLE} c WHERE c.session_id = o.session_id "
        "AND (c.capture_state != 'paired' OR c.outcome = 'unknown'))"
    )
    compatible_params = (*params, OBSERVER_SCHEMA_VERSION)
    for row in conn.execute(
        f"SELECT c.outcome, COUNT(*) AS n FROM {_CALLS_TABLE} c "
        f"JOIN ({compatible}) eligible ON eligible.session_id = c.session_id "
        "WHERE c.outcome IN ('ok', 'failed') GROUP BY c.outcome",
        compatible_params,
    ):
        rate_counts[row["outcome"]] = int(row["n"])
    rate_counts["denominator"] = rate_counts["ok"] + rate_counts["failed"]

    total_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM sessions s WHERE {predicate}", params
    ).fetchone()[0])
    captured_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM {_SESSIONS_TABLE} o JOIN sessions s ON s.id = o.session_id "
        f"WHERE {predicate}", params
    ).fetchone()[0])
    observed_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM ({compatible})", compatible_params
    ).fetchone()[0])
    partial_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM {_SESSIONS_TABLE} o JOIN sessions s ON s.id = o.session_id "
        f"WHERE {predicate} AND o.observer_schema_version = ? "
        f"AND EXISTS (SELECT 1 FROM {_CALLS_TABLE} c WHERE c.session_id = o.session_id "
        "AND (c.capture_state != 'paired' OR c.outcome = 'unknown'))",
        compatible_params,
    ).fetchone()[0])
    unknown_sessions = max(0, captured_sessions - observed_sessions - partial_sessions)
    success_rate = (
        rate_counts["ok"] / rate_counts["denominator"] if rate_counts["denominator"] else None
    )
    return {
        "window": window,
        "reset_cutoff": reset_cutoff,
        "calls": counts,
        "rate_calls": rate_counts,
        "success_rate": success_rate,
        "total_sessions": total_sessions,
        "included_sessions": total_sessions,
        "captured_sessions": captured_sessions,
        "observed_sessions": observed_sessions,
        "rate_included_sessions": observed_sessions,
        "partial_sessions": partial_sessions,
        "unknown_sessions": unknown_sessions,
        "excluded_sessions": max(0, total_sessions - captured_sessions),
        "rate_excluded_sessions": max(0, total_sessions - observed_sessions),
    }


def repeated_failure_findings(
    conn: sqlite3.Connection,
    *,
    window: str = "Overall",
    now: float | None = None,
    timezone_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic cross-session failure evidence for the selected window."""
    current = float(now if now is not None else time.time())
    predicate, params = _session_window(
        window, current, _reset_cutoff(conn), timezone_name=timezone_name
    )
    population = metrics(conn, window=window, now=current, timezone_name=timezone_name)
    rows = conn.execute(
        f"SELECT c.id, c.session_id, c.tool_name, c.turn_id, c.api_request_id, c.tool_call_id, c.started_at, "
        "c.completed_at, c.outcome, c.error_type, c.error_code, c.error_message, c.failure_status "
        f"FROM {_CALLS_TABLE} c JOIN sessions s ON s.id = c.session_id "
        f"WHERE {predicate} AND c.outcome = 'failed' "
        "ORDER BY COALESCE(c.completed_at, c.started_at), c.id",
        params,
    ).fetchall()
    provider_rows = conn.execute(
        f"SELECT p.session_id, p.turn_id, p.api_request_id, p.retry_count, p.capture_state "
        f"FROM {_PROVIDER_ATTEMPTS_TABLE} p JOIN sessions s ON s.id = p.session_id "
        f"WHERE {predicate} AND p.observer_schema_version = ? ORDER BY p.started_at, p.id",
        (*params, OBSERVER_SCHEMA_VERSION),
    ).fetchall()
    return repeated_failures.build_findings(
        rows,
        population_excluded_sessions=population["rate_excluded_sessions"],
        provider_attempts=provider_rows,
    )
