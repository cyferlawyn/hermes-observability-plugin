"""Deterministic cross-session grouping of explicit tool-call failures."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ISO_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})?\b"
)
_GENERIC_ERROR_TYPES = {"tool_error", "error", "exception", "unknown"}
_TIMEOUT_STATUSES = {"timeout", "timed_out", "timed-out"}


def _message_signature(message: str) -> tuple[tuple[tuple[str, ...], ...], str, str]:
    """Mask only RFC3339-like timestamps and UUIDs; leave other values literal."""
    token_pattern = re.compile(
        f"(?P<timestamp>{_ISO_TIMESTAMP.pattern})|(?P<uuid>{_UUID.pattern})"
    )
    parts: list[tuple[str, ...]] = []
    rendered: list[str] = []
    masked_kinds: set[str] = set()
    cursor = 0
    for match in token_pattern.finditer(message):
        if match.start() > cursor:
            literal = message[cursor:match.start()]
            parts.append(("text", literal))
            rendered.append(literal)
        kind = str(match.lastgroup)
        parts.append((kind,))
        masked_kinds.add(kind)
        rendered.append("<TIMESTAMP>" if kind == "timestamp" else "<UUID>")
        cursor = match.end()
    if cursor < len(message):
        literal = message[cursor:]
        parts.append(("text", literal))
        rendered.append(literal)
    if not masked_kinds:
        basis = "exact_error_message"
    elif masked_kinds == {"uuid", "timestamp"}:
        basis = "exact_message_masked_uuid_timestamp"
    elif "uuid" in masked_kinds:
        basis = "exact_message_masked_uuid"
    else:
        basis = "exact_message_masked_timestamp"
    return tuple(parts), basis, "".join(rendered)


def _signature(row: dict[str, Any]) -> tuple[tuple[str, Any], str, str] | None:
    error_code = str(row.get("error_code") or "")
    if error_code.strip():
        return ("error_code", error_code), "structured_error_code", error_code

    error_type = str(row.get("error_type") or "")
    if error_type.strip() and error_type.strip().casefold() not in _GENERIC_ERROR_TYPES:
        return ("error_type", error_type), "structured_error_type", error_type

    message = str(row.get("error_message") or "")
    if message.strip():
        key, basis, display = _message_signature(message)
        return ("error_message", key), basis, display
    return None


def _observed_at(row: dict[str, Any]) -> float | None:
    value = row.get("completed_at")
    if value is None:
        value = row.get("started_at")
    return float(value) if value is not None else None


def _is_timeout(row: dict[str, Any]) -> bool:
    status = str(row.get("failure_status") or "").casefold()
    error_type = str(row.get("error_type") or "").casefold()
    return status in _TIMEOUT_STATUSES or error_type == "timeout" or error_type.endswith("timeouterror")


def build_findings(
    rows: Iterable[dict[str, Any]], *, population_excluded_sessions: int = 0
) -> list[dict[str, Any]]:
    """Group by exact observer tool identity and deterministic error signature.

    Tool-call IDs are scoped by source session, tool, turn, and API request.
    Repeated telemetry rows with the same full key count once; rows without a
    correlation ID remain individually identifiable by their observer event ID.
    """
    groups: dict[tuple[str, Any], list[tuple[dict[str, Any], str, str]]] = defaultdict(list)
    seen_invocations: set[tuple[str, ...]] = set()

    ordered_rows = sorted(
        (dict(row) for row in rows),
        key=lambda row: (_observed_at(row) or 0.0, str(row.get("id") or "")),
    )
    for row in ordered_rows:
        if row.get("outcome") != "failed":
            continue
        raw_tool_name = str(row.get("tool_name") or "")
        if not raw_tool_name.strip() or raw_tool_name.strip().casefold() == "unknown":
            continue
        tool_name = raw_tool_name
        session_id = str(row.get("session_id") or "")
        call_id = str(row.get("id") or "")
        tool_call_id = str(row.get("tool_call_id") or "")
        if tool_call_id:
            invocation = (
                "tool_call_id", session_id, tool_name,
                str(row.get("turn_id") or ""),
                str(row.get("api_request_id") or ""),
                tool_call_id,
            )
        else:
            invocation = ("event_id", session_id, tool_name, call_id)
        signature = _signature(row)
        if signature is None:
            continue
        if invocation in seen_invocations:
            continue
        seen_invocations.add(invocation)

        signature_key, match_basis, display_signature = signature
        tool_name = str(row.get("tool_name") or "unknown")
        groups[(tool_name, signature_key)].append((row, match_basis, display_signature))

    findings = []
    for (tool_name, _signature_key), calls in groups.items():
        sessions = {str(row.get("session_id") or "") for row, _, _ in calls}
        if len(calls) < 2 or len(sessions) < 2:
            continue

        calls.sort(key=lambda item: (_observed_at(item[0]) or 0.0, str(item[0].get("id") or "")))
        first_row, basis, display_signature = calls[0]
        timestamps = [value for row, _, _ in calls if (value := _observed_at(row)) is not None]
        message = str(first_row.get("error_message") or "")
        error_fallback = next(
            (
                str(first_row.get(field) or "")
                for field in ("error_code", "error_type", "failure_status")
                if str(first_row.get(field) or "").strip()
            ),
            "Explicit failure",
        )
        error_excerpt = message[:240] if message.strip() else error_fallback[:240]
        example_calls = []
        example_sessions: set[str] = set()
        example_call_ids: set[str] = set()
        for row, _, _ in calls:
            session_id = str(row.get("session_id") or "")
            call_id = str(row.get("id") or "")
            if session_id in example_sessions:
                continue
            example_calls.append((row, call_id))
            example_sessions.add(session_id)
            example_call_ids.add(call_id)
            if len(example_calls) == 5:
                break
        for row, _, _ in calls:
            call_id = str(row.get("id") or "")
            if len(example_calls) == 5:
                break
            if call_id in example_call_ids:
                continue
            example_calls.append((row, call_id))
            example_call_ids.add(call_id)
        example_calls.sort(key=lambda item: (_observed_at(item[0]) or 0.0, item[1]))
        examples = [
            {
                "session_id": str(row.get("session_id") or ""),
                "call_id": call_id,
                "observed_at": _observed_at(row),
            }
            for row, call_id in example_calls
        ]
        findings.append({
            "tool_name": tool_name,
            "identity_kind": "mcp" if tool_name.startswith("mcp__") else "tool",
            "signature": display_signature,
            "match_basis": basis,
            "distinct_failed_calls": len(calls),
            "affected_sessions": len(sessions),
            "first_observed_at": min(timestamps) if timestamps else None,
            "last_observed_at": max(timestamps) if timestamps else None,
            "timeout_count": sum(1 for row, _, _ in calls if _is_timeout(row)),
            # API retries are a separate observer event family and are not captured
            # by this plugin version; never substitute a made-up zero.
            "retry_count": None,
            "retry_state": "not_captured",
            "error_excerpt": error_excerpt,
            "at_least_n_observed": population_excluded_sessions > 0,
            "population_excluded_sessions": max(0, int(population_excluded_sessions)),
            "examples": examples,
        })

    findings.sort(key=lambda item: (item["tool_name"].casefold(), item["signature"].casefold(), item["match_basis"]))
    return findings
