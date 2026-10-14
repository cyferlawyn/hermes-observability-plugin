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

SCHEMA_VERSION = 3
OBSERVER_SCHEMA_VERSION = "hermes.observer.v1"
_META_TABLE = "hermes_observability_meta"
_SESSIONS_TABLE = "hermes_observability_sessions"
_CALLS_TABLE = "hermes_observability_calls"
_PROVIDER_ATTEMPTS_TABLE = "hermes_observability_provider_attempts"
_FAMILY_COVERAGE_TABLE = "hermes_observability_family_coverage"

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
    return repeated_failures.build_findings(
        rows, population_excluded_sessions=population["rate_excluded_sessions"]
    )
