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
        self._pending: dict[tuple[str, str, str], deque[str]] = defaultdict(deque)
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
                    store.register_session(
                        conn, session_id,
                        observer_schema_version=_observer_schema_version(kwargs),
                    )
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
                    store.register_session(
                        conn, session_id,
                        observer_schema_version=_observer_schema_version(kwargs),
                    )
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
                match_key = (session_id, "id", tool_call_id) if tool_call_id else (session_id, "name", tool_name)
                with self._lock:
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
        match_key = (session_id, "id", tool_call_id) if tool_call_id else (session_id, "name", tool_name)
        with self._lock:
            pending = self._pending.get(match_key)
            event_id = pending.popleft() if pending else None
            if pending is not None and not pending:
                self._pending.pop(match_key, None)
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
                        error_message=kwargs.get("error_message"),
                    )
        except Exception:
            log.warning("observability: failed to record tool completion", exc_info=True)

    def _is_eligible(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._eligible


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
    ):
        ctx.register_hook(name, callback)
    return collector
