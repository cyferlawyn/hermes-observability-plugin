"""Read models shared by the Desktop API and tests."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import analyze_prompt, source_detail, store


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
        "subagents": store.subagent_metrics(
            conn, window=window, now=now, timezone_name=timezone_name
        ),
        "tool_breakdown": store.tool_breakdown(
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
    calls = store.session_trace(conn, session_id)
    provider_attempts = store.session_provider_attempts(conn, session_id)
    approvals = store.session_approvals(conn, session_id)
    subagents = store.session_subagents(conn, session_id)
    timeline = _chronological_timeline(
        conn, session_id, captured=session.get("captured_at") is not None,
        calls=calls, provider_attempts=provider_attempts, approvals=approvals,
        subagents=subagents,
    )
    return {
        "window": window,
        "session": session,
        "calls": calls,
        "provider_attempts": provider_attempts,
        "provider_coverage": store.provider_session_coverage(conn, session_id),
        "approvals": approvals,
        "approval_coverage": store.approval_session_coverage(conn, session_id),
        "subagents": subagents,
        "subagent_coverage": store.subagent_session_coverage(conn, session_id),
        "timeline": timeline,
    }


def _time_key(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _first_available(row: dict[str, Any], first: str, second: str) -> Any:
    value = row.get(first)
    return value if value is not None else row.get(second)


def _chronological_timeline(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    captured: bool,
    calls: list[dict[str, Any]],
    provider_attempts: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    subagents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge visible source messages and observer events without persisting content."""
    timeline: list[dict[str, Any]] = []
    if captured:
        for message in source_detail.visible_messages(conn, session_id):
            timeline.append({
                **message,
                "id": f"message:{message['source_message_id']}",
                "kind": "message",
            })
    families = (
        ("tool_call", calls, lambda row: _first_available(row, "started_at", "completed_at")),
        ("provider_attempt", provider_attempts,
         lambda row: _first_available(row, "started_at", "ended_at")),
        ("approval", approvals,
         lambda row: _first_available(row, "requested_at", "decided_at")),
        ("subagent", subagents,
         lambda row: _first_available(row, "started_at", "stopped_at")),
    )
    for kind, events, timestamp_of in families:
        for event in events:
            item = {**event, "kind": kind, "timestamp": timestamp_of(event)}
            timeline.append(item)

    priority = {"message": 0, "provider_attempt": 1, "tool_call": 2, "approval": 3, "subagent": 4}
    def order_key(item: dict[str, Any]) -> tuple[Any, ...]:
        timestamp = _time_key(item.get("timestamp"))
        if timestamp is not None:
            return (0, timestamp, priority.get(item["kind"], 9), str(item.get("id", "")))
        # Un-timestamped events remain visible but are explicitly placed after
        # known-time records; source IDs provide deterministic, not inferred,
        # ordering among them.
        return (1, priority.get(item["kind"], 9), str(item.get("id", "")))
    return sorted(timeline, key=order_key)


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
    detail = {
        "available": source["status"] == "matched",
        "call": call,
        "source": source,
        "context": context,
    }
    detail["analyze_prompt"] = analyze_prompt.build_analyze_prompt(detail)
    return detail
