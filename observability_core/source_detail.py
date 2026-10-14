"""On-demand, exact-ID reads from Hermes' canonical visible message history."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


_EMPTY = {"status": "missing", "arguments": None, "result": None, "result_status": "missing"}


def resolve_call(
    conn: sqlite3.Connection, session_id: str, tool_name: str,
    source_tool_call_id: str | None,
) -> dict[str, Any]:
    """Resolve a captured invocation without fuzzy matching or reading private sidecars."""
    if not source_tool_call_id:
        return dict(_EMPTY)

    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    }
    required = {"id", "session_id", "role", "content", "tool_calls", "tool_call_id"}
    if not required.issubset(columns):
        return dict(_EMPTY)

    id_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    rows = conn.execute(
        "SELECT id, content, tool_calls FROM messages "
        "WHERE session_id = ? AND role = 'assistant' AND tool_calls IS NOT NULL",
        (session_id,),
    ).fetchall()
    for row in rows:
        try:
            calls = json.loads(row["tool_calls"])
        except (TypeError, ValueError):
            continue
        if not isinstance(calls, list):
            continue
        for source_call in calls:
            if not isinstance(source_call, dict) or source_call.get("id") != source_tool_call_id:
                continue
            id_matches.append((dict(row), source_call))

    if len(id_matches) > 1:
        result = dict(_EMPTY)
        result["status"] = "ambiguous"
        result["result_status"] = "ambiguous"
        return result
    if not id_matches:
        return dict(_EMPTY)

    source_message, source_call = id_matches[0]
    function = source_call.get("function")
    function = function if isinstance(function, dict) else {}
    source_name = function.get("name", source_call.get("name"))
    if source_name != tool_name:
        return dict(_EMPTY)

    arguments = function.get("arguments", source_call.get("arguments"))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            pass

    result_rows = conn.execute(
        "SELECT content FROM messages WHERE session_id = ? AND role = 'tool' AND tool_call_id = ? "
        "ORDER BY id",
        (session_id, source_tool_call_id),
    ).fetchall()
    if len(result_rows) == 1:
        result_text = result_rows[0]["content"]
        result_status = "matched"
    elif len(result_rows) > 1:
        result_text = None
        result_status = "ambiguous"
    else:
        result_text = None
        result_status = "missing"

    context = _visible_context(conn, session_id, int(source_message["id"]))
    return {
        "status": "matched",
        "arguments": arguments,
        "result": result_text,
        "result_status": result_status,
        "source_message_id": source_message["id"],
        "context": context,
    }


def _visible_context(conn: sqlite3.Connection, session_id: str, message_id: int) -> list[dict[str, Any]]:
    """Return a small window of visible user/assistant content, excluding sidecars."""
    has_timestamp = "timestamp" in {
        row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    }
    timestamp_select = ", timestamp" if has_timestamp else ""
    before = conn.execute(
        "SELECT id, role, content" + timestamp_select + " FROM messages "
        "WHERE session_id = ? AND id < ? AND role IN ('user', 'assistant') AND content IS NOT NULL "
        "AND content != '' ORDER BY id DESC LIMIT 3",
        (session_id, message_id),
    ).fetchall()
    current = conn.execute(
        "SELECT id, role, content" + timestamp_select + " FROM messages "
        "WHERE session_id = ? AND id = ? AND role IN ('user', 'assistant') AND content IS NOT NULL "
        "AND content != ''",
        (session_id, message_id),
    ).fetchall()
    after = conn.execute(
        "SELECT id, role, content" + timestamp_select + " FROM messages "
        "WHERE session_id = ? AND id > ? AND role IN ('user', 'assistant') AND content IS NOT NULL "
        "AND content != '' ORDER BY id LIMIT 3",
        (session_id, message_id),
    ).fetchall()
    items = [*reversed(before), *current, *after]
    return [
        {key: row[key] for key in ("role", "content", "timestamp") if key in row.keys()}
        for row in items
    ]
