"""Fail-open, prospective capture of tool/MCP observer events."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable

from . import store

log = logging.getLogger(__name__)


class Collector:
    """Capture tool lifecycle metadata only for sessions seen starting after enablement."""

    def __init__(self, connection_factory: Callable[[], Any]):
        self._connection_factory = connection_factory
        self._eligible: set[str] = set()
        self._pending: dict[tuple[str, ...], deque[str]] = defaultdict(deque)
        self._ambiguous_pending_keys: set[tuple[str, ...]] = set()
        self._provider_pending: dict[tuple[str, ...], str] = {}
        self._ambiguous_provider_pending_keys: set[tuple[str, ...]] = set()
        self._approval_pending: dict[tuple[str, ...], str] = {}
        self._ambiguous_approval_keys: set[tuple[str, ...]] = set()
        self._quarantined_approval_keys: set[tuple[str, ...]] = set()
        self._lock = threading.RLock()

    def _connection(self):
        return self._connection_factory()

    def on_session_start(self, **kwargs: Any) -> None:
        session_id = str(kwargs.get("session_id") or "")
        if not session_id:
            return
        with self._lock:
            self._eligible.add(session_id)
        try:
            with self._connection() as conn:
                if conn is not None:
                    version = _observer_schema_version(kwargs)
                    store.register_session(conn, session_id, observer_schema_version=version)
                    store.link_subagent_child_session(conn, session_id)
                    store.register_provider_family(conn, session_id, version)
                    store.register_approval_family(conn, session_id, version)
                    store.register_subagent_family(conn, session_id, version)
        except Exception:
            log.warning("observability: failed to mark session start", exc_info=True)

    def on_session_end(self, **kwargs: Any) -> None:
        """The end hook is run-scoped; retain eligibility for later turns in this session."""
        session_id = str(kwargs.get("session_id") or "")
        if not session_id or not self._is_eligible(session_id):
            return
        try:
            with self._connection() as conn:
                if conn is not None:
                    version = _observer_schema_version(kwargs)
                    store.register_session(conn, session_id, observer_schema_version=version)
                    store.link_subagent_child_session(conn, session_id)
                    store.register_provider_family(conn, session_id, version)
                    store.register_approval_family(conn, session_id, version)
                    store.register_subagent_family(conn, session_id, version)
        except Exception:
            log.warning("observability: failed to close session run", exc_info=True)

    def on_session_finalize(self, **kwargs: Any) -> None:
        session_id = str(kwargs.get("session_id") or "")
        if not session_id:
            return
        with self._lock:
            self._eligible.discard(session_id)
            for key in [key for key in self._pending if key[0] == session_id]:
                self._pending.pop(key, None)
            self._ambiguous_pending_keys = {
                key for key in self._ambiguous_pending_keys if key[0] != session_id
            }
            self._provider_pending = {
                key: event_id for key, event_id in self._provider_pending.items()
                if key[0] != session_id
            }
            self._ambiguous_provider_pending_keys = {
                key for key in self._ambiguous_provider_pending_keys if key[0] != session_id
            }
            self._approval_pending = {
                key: event_id for key, event_id in self._approval_pending.items()
                if key[0] != session_id
            }
            self._ambiguous_approval_keys = {
                key for key in self._ambiguous_approval_keys if key[0] != session_id
            }
            self._quarantined_approval_keys = {
                key for key in self._quarantined_approval_keys if key[0] != session_id
            }

    def on_pre_tool_call(self, **kwargs: Any) -> None:
        session_id = str(kwargs.get("session_id") or "")
        if not session_id or not self._is_eligible(session_id):
            return
        tool_name = str(kwargs.get("tool_name") or "unknown")
        tool_call_id = str(kwargs.get("tool_call_id") or "") or None
        try:
            with self._connection() as conn:
                if conn is None or not store.register_session(
                    conn, session_id,
                    observer_schema_version=_observer_schema_version(kwargs),
                ):
                    return
                event_id = store.begin_tool_call(
                    conn,
                    session_id,
                    tool_name,
                    started_at=time.time(),
                    task_id=_optional_text(kwargs.get("task_id")),
                    turn_id=_optional_text(kwargs.get("turn_id")),
                    api_request_id=_optional_text(kwargs.get("api_request_id")),
                    tool_call_id=tool_call_id,
                )
            if event_id:
                match_key = _tool_call_match_key(
                    session_id, tool_name, tool_call_id, kwargs.get("turn_id"), kwargs.get("api_request_id")
                )
                with self._lock:
                    if match_key not in self._ambiguous_pending_keys:
                        self._pending[match_key].append(event_id)
        except Exception:
            # Hermes observer hooks are fail-open; telemetry must never break the agent loop.
            log.warning("observability: failed to record tool start", exc_info=True)

    def on_post_tool_call(self, **kwargs: Any) -> None:
        session_id = str(kwargs.get("session_id") or "")
        if not session_id or not self._is_eligible(session_id):
            return
        tool_name = str(kwargs.get("tool_name") or "unknown")
        tool_call_id = str(kwargs.get("tool_call_id") or "") or None
        match_key = _tool_call_match_key(
            session_id, tool_name, tool_call_id, kwargs.get("turn_id"), kwargs.get("api_request_id")
        )
        with self._lock:
            pending = self._pending.get(match_key)
            if match_key in self._ambiguous_pending_keys:
                event_id = None
            elif pending and (tool_call_id or len(pending) == 1):
                event_id = pending.popleft()
                if not pending:
                    self._pending.pop(match_key, None)
            else:
                event_id = None
                if pending:
                    # Without a call ID, multiple starts sharing the remaining
                    # key cannot be safely correlated to this completion. Quarantine
                    # the key until session finalization in case late hooks arrive.
                    self._pending.pop(match_key, None)
                    self._ambiguous_pending_keys.add(match_key)
        try:
            with self._connection() as conn:
                if conn is None or not store.register_session(
                    conn, session_id,
                    observer_schema_version=_observer_schema_version(kwargs),
                ):
                    return
                if event_id:
                    completed = store.complete_tool_call(
                        conn,
                        event_id,
                        kwargs.get("status"),
                        completed_at=time.time(),
                        duration_ms=_optional_float(kwargs.get("duration_ms")),
                        error_type=kwargs.get("error_type"),
                        error_code=kwargs.get("error_code"),
                        error_message=kwargs.get("error_message"),
                    )
                    if not completed:
                        store.record_post_only_call(
                            conn,
                            session_id,
                            tool_name,
                            kwargs.get("status"),
                            completed_at=time.time(),
                            duration_ms=_optional_float(kwargs.get("duration_ms")),
                            task_id=_optional_text(kwargs.get("task_id")),
                            turn_id=_optional_text(kwargs.get("turn_id")),
                            api_request_id=_optional_text(kwargs.get("api_request_id")),
                            tool_call_id=tool_call_id,
                            error_type=kwargs.get("error_type"),
                            error_code=kwargs.get("error_code"),
                            error_message=kwargs.get("error_message"),
                        )
                else:
                    store.record_post_only_call(
                        conn,
                        session_id,
                        tool_name,
                        kwargs.get("status"),
                        completed_at=time.time(),
                        duration_ms=_optional_float(kwargs.get("duration_ms")),
                        task_id=_optional_text(kwargs.get("task_id")),
                        turn_id=_optional_text(kwargs.get("turn_id")),
                        api_request_id=_optional_text(kwargs.get("api_request_id")),
                        tool_call_id=tool_call_id,
                        error_type=kwargs.get("error_type"),
                        error_code=kwargs.get("error_code"),
                        error_message=kwargs.get("error_message"),
                    )
        except Exception:
            log.warning("observability: failed to record tool completion", exc_info=True)

    def on_pre_api_request(self, **kwargs: Any) -> None:
        """Capture the documented API-attempt metadata without retaining payloads."""
        session_id = str(kwargs.get("session_id") or "")
        if not session_id or not self._is_eligible(session_id):
            return
        version = _observer_schema_version(kwargs)
        try:
            with self._connection() as conn:
                if conn is None or not store.register_session(
                    conn, session_id, observer_schema_version=version
                ):
                    return
                store.register_provider_family(conn, session_id, version)
                event_id = store.begin_provider_attempt(
                    conn,
                    session_id,
                    task_id=kwargs.get("task_id"),
                    turn_id=kwargs.get("turn_id"),
                    api_request_id=kwargs.get("api_request_id"),
                    api_call_count=kwargs.get("api_call_count"),
                    provider=kwargs.get("provider"),
                    model=kwargs.get("model"),
                    started_at=kwargs.get("started_at"),
                    observer_schema_version=version,
                )
            key = _provider_attempt_match_key(kwargs)
            if event_id and key:
                with self._lock:
                    if key in self._ambiguous_provider_pending_keys:
                        return
                    if key in self._provider_pending:
                        self._provider_pending.pop(key, None)
                        self._ambiguous_provider_pending_keys.add(key)
                    else:
                        self._provider_pending[key] = event_id
        except Exception:
            log.warning("observability: failed to record provider attempt start", exc_info=True)

    def on_post_api_request(self, **kwargs: Any) -> None:
        self._finish_provider_attempt(kwargs, outcome="ok")

    def on_api_request_error(self, **kwargs: Any) -> None:
        self._finish_provider_attempt(kwargs, outcome="failed")

    def _finish_provider_attempt(self, kwargs: dict[str, Any], *, outcome: str) -> None:
        session_id = str(kwargs.get("session_id") or "")
        if not session_id or not self._is_eligible(session_id):
            return
        key = _provider_attempt_match_key(kwargs)
        with self._lock:
            if key and key not in self._ambiguous_provider_pending_keys:
                event_id = self._provider_pending.pop(key, None)
            else:
                event_id = None
        version = _observer_schema_version(kwargs)
        try:
            with self._connection() as conn:
                if conn is None or not store.register_session(
                    conn, session_id, observer_schema_version=version
                ):
                    return
                store.register_provider_family(conn, session_id, version)
                completed = bool(
                    event_id and store.complete_provider_attempt(
                        conn, event_id, kwargs, outcome=outcome
                    )
                )
                if not completed:
                    store.record_post_only_provider_attempt(
                        conn, session_id, kwargs, outcome=outcome,
                        observer_schema_version=version,
                    )
        except Exception:
            log.warning("observability: failed to record provider attempt completion", exc_info=True)

    def on_pre_approval_request(self, **kwargs: Any) -> None:
        """Capture approval request metadata only; never persist command text."""
        session_id = _approval_session_id(kwargs, self._is_eligible)
        if not session_id:
            return
        version = _observer_schema_version(kwargs)
        key = _approval_match_key(session_id, kwargs)
        try:
            with self._lock:
                with self._connection() as conn:
                    if conn is None or not store.register_session(
                        conn, session_id, observer_schema_version=version
                    ):
                        return
                    store.register_approval_family(conn, session_id, version)
                    key_seen = bool(key and store.approval_key_seen(conn, key))
                    event_id = store.begin_approval_request(
                        conn,
                        session_id,
                        session_key=kwargs.get("session_key"),
                        turn_id=kwargs.get("turn_id"),
                        tool_call_id=kwargs.get("tool_call_id"),
                        surface=kwargs.get("surface"),
                        pattern_key=kwargs.get("pattern_key"),
                        pattern_keys=kwargs.get("pattern_keys"),
                        requested_at=time.time(),
                        observer_schema_version=version,
                    )
                if event_id and key:
                    if key_seen:
                        self._approval_pending.pop(key, None)
                        self._ambiguous_approval_keys.add(key)
                        self._quarantined_approval_keys.add(key)
                        return
                    if key in self._ambiguous_approval_keys or key in self._quarantined_approval_keys:
                        return
                    if key in self._approval_pending:
                        self._approval_pending.pop(key, None)
                        self._ambiguous_approval_keys.add(key)
                    else:
                        self._approval_pending[key] = event_id
        except Exception:
            log.warning("observability: failed to record approval request", exc_info=True)

    def on_post_approval_response(self, **kwargs: Any) -> None:
        """Capture an explicit decision, pairing only on unambiguous supplied IDs."""
        session_id = _approval_session_id(kwargs, self._is_eligible)
        if not session_id:
            return
        key = _approval_match_key(session_id, kwargs)
        with self._lock:
            if key and key not in self._ambiguous_approval_keys and key not in self._quarantined_approval_keys:
                event_id = self._approval_pending.pop(key, None)
                self._quarantined_approval_keys.add(key)
            else:
                event_id = None
        version = _observer_schema_version(kwargs)
        try:
            with self._connection() as conn:
                if conn is None or not store.register_session(
                    conn, session_id, observer_schema_version=version
                ):
                    return
                store.register_approval_family(conn, session_id, version)
                completed = bool(
                    event_id and store.complete_approval_request(
                        conn,
                        event_id,
                        choice=kwargs.get("choice"),
                        decided_by=kwargs.get("decided_by"),
                        decided_at=time.time(),
                        observer_schema_version=version,
                    )
                )
                if not completed:
                    store.record_post_only_approval(
                        conn,
                        session_id,
                        session_key=kwargs.get("session_key"),
                        turn_id=kwargs.get("turn_id"),
                        tool_call_id=kwargs.get("tool_call_id"),
                        surface=kwargs.get("surface"),
                        pattern_key=kwargs.get("pattern_key"),
                        pattern_keys=kwargs.get("pattern_keys"),
                        choice=kwargs.get("choice"),
                        decided_by=kwargs.get("decided_by"),
                        decided_at=time.time(),
                        observer_schema_version=version,
                    )
        except Exception:
            log.warning("observability: failed to record approval response", exc_info=True)

    def on_subagent_start(self, **kwargs: Any) -> None:
        """Capture the documented child-start metadata, without child content."""
        parent_id = _nonblank_identity(kwargs.get("parent_session_id"))
        session_id = parent_id or _nonblank_identity(kwargs.get("session_id"))
        if not session_id or not self._is_eligible(session_id):
            return
        version = _observer_schema_version(kwargs)
        try:
            with self._connection() as conn:
                if conn is None or not store.register_session(
                    conn, session_id, observer_schema_version=version
                ):
                    return
                store.register_subagent_family(conn, session_id, version)
                store.begin_subagent(
                    conn, session_id,
                    parent_session_id=parent_id,
                    child_session_id=kwargs.get("child_session_id"),
                    parent_turn_id=kwargs.get("parent_turn_id"),
                    parent_subagent_id=kwargs.get("parent_subagent_id"),
                    child_subagent_id=kwargs.get("child_subagent_id"),
                    child_role=kwargs.get("child_role"),
                    child_goal=kwargs.get("child_goal"),
                    started_at=time.time(),
                    observer_schema_version=version,
                )
        except Exception:
            log.warning("observability: failed to record subagent start", exc_info=True)

    def on_subagent_stop(self, **kwargs: Any) -> None:
        """Capture a child stop, pairing only a unique supplied identity."""
        parent_id = _nonblank_identity(kwargs.get("parent_session_id"))
        session_id = parent_id or _nonblank_identity(kwargs.get("session_id"))
        if not session_id or not self._is_eligible(session_id):
            return
        version = _observer_schema_version(kwargs)
        event = {
            **kwargs,
            "stopped_at": time.time(),
            "telemetry_schema_version": version,
        }
        try:
            with self._connection() as conn:
                if conn is None or not store.register_session(
                    conn, session_id, observer_schema_version=version
                ):
                    return
                store.register_subagent_family(conn, session_id, version)
                if not store.complete_matching_subagent(conn, session_id, event):
                    store.record_unmatched_subagent_stop(
                        conn, session_id, event, parent_session_id=parent_id
                    )
        except Exception:
            log.warning("observability: failed to record subagent stop", exc_info=True)

    def _is_eligible(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._eligible


def _nonblank_identity(value: Any) -> str | None:
    """Treat absent/blank IDs as unknown while preserving nonblank IDs verbatim."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value)
    return text if text.strip() else None


def _tool_call_match_key(
    session_id: str,
    tool_name: str,
    tool_call_id: str | None,
    turn_id: Any,
    api_request_id: Any,
) -> tuple[str, ...]:
    if tool_call_id:
        return (
            session_id,
            "id",
            tool_name,
            _optional_text(turn_id) or "",
            _optional_text(api_request_id) or "",
            tool_call_id,
        )
    return (
        session_id,
        "name",
        tool_name,
        _optional_text(turn_id) or "",
        _optional_text(api_request_id) or "",
    )


def _provider_attempt_match_key(kwargs: dict[str, Any]) -> tuple[str, ...] | None:
    session_id = _optional_text(kwargs.get("session_id"))
    request_id = _optional_text(kwargs.get("api_request_id"))
    if not session_id or not request_id or not request_id.strip():
        return None
    return (
        session_id,
        _optional_text(kwargs.get("task_id")) or "",
        _optional_text(kwargs.get("turn_id")) or "",
        request_id,
    )


def _approval_session_id(kwargs: dict[str, Any], is_eligible: Callable[[str], bool]) -> str | None:
    for key in ("session_id", "session_key"):
        value = _optional_text(kwargs.get(key))
        if value and is_eligible(value):
            return value
    return None


def _approval_match_key(session_id: str, kwargs: dict[str, Any]) -> tuple[str, ...] | None:
    turn_id = _optional_text(kwargs.get("turn_id"))
    tool_call_id = _optional_text(kwargs.get("tool_call_id"))
    if not any(value is not None and value.strip() for value in (turn_id, tool_call_id)):
        return None
    return (
        session_id,
        store.approval_surface(kwargs.get("surface")),
        turn_id or "",
        tool_call_id or "",
    )


def _observer_schema_version(kwargs: dict[str, Any]) -> str:
    value = kwargs.get("telemetry_schema_version")
    return str(value) if value not in (None, "") else "unknown"


def _optional_text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def register(ctx: Any, connection_factory: Callable[[], Any]) -> Collector:
    collector = Collector(connection_factory)
    for name, callback in (
        ("on_session_start", collector.on_session_start),
        ("on_session_end", collector.on_session_end),
        ("on_session_finalize", collector.on_session_finalize),
        ("pre_tool_call", collector.on_pre_tool_call),
        ("post_tool_call", collector.on_post_tool_call),
        ("pre_api_request", collector.on_pre_api_request),
        ("post_api_request", collector.on_post_api_request),
        ("api_request_error", collector.on_api_request_error),
        ("pre_approval_request", collector.on_pre_approval_request),
        ("post_approval_response", collector.on_post_approval_response),
        ("subagent_start", collector.on_subagent_start),
        ("subagent_stop", collector.on_subagent_stop),
    ):
        ctx.register_hook(name, callback)
    return collector
