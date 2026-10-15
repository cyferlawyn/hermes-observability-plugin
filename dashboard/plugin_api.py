"""Profile-local observability API mounted under /api/plugins/hermes-observability-plugin/."""
from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
_CORE_MODULE = "hermes_observability_plugin_core"
_CORE_DIR = Path(__file__).resolve().parents[1] / "observability_core"


def _core() -> tuple[Any, Any, Any]:
    if _CORE_MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _CORE_MODULE,
            _CORE_DIR / "__init__.py",
            submodule_search_locations=[str(_CORE_DIR)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("observability core package could not be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_CORE_MODULE] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(_CORE_MODULE, None)
            raise
    store = importlib.import_module(f"{_CORE_MODULE}.store")
    service = importlib.import_module(f"{_CORE_MODULE}.service")
    runtime = importlib.import_module(f"{_CORE_MODULE}.runtime")
    return store, service, runtime


def _validate_profile(runtime: Any, expected_profile: str | None) -> str | None:
    """Check a renderer's captured profile against the backend's routed profile."""
    actual_profile = runtime.profile_name()
    if expected_profile is not None and (not actual_profile or expected_profile != actual_profile):
        raise HTTPException(status_code=409, detail="Connected profile changed; no cross-profile action was taken.")
    return actual_profile


def _unavailable(window: str = "Overall") -> dict[str, Any]:
    return {
        "available": False,
        "reason": "No readable session database is available for this connected profile.",
        "window": window,
        "metrics": {
            "window": window,
            "reset_cutoff": None,
            "calls": {"total": 0, "ok": 0, "failed": 0, "not_executed": 0, "unknown": 0},
            "rate_calls": {"ok": 0, "failed": 0, "denominator": 0},
            "success_rate": None,
            "total_sessions": 0,
            "included_sessions": 0,
            "captured_sessions": 0,
            "observed_sessions": 0,
            "rate_included_sessions": 0,
            "partial_sessions": 0,
            "unknown_sessions": 0,
            "excluded_sessions": 0,
            "rate_excluded_sessions": 0,
        },
        "provider_attempts": {
            "window": window,
            "attempts": {"total": 0, "ok": 0, "failed": 0, "unknown": 0},
            "unpaired_events": 0,
            "retryable_failures": 0,
            "success_rate": None,
            "total_sessions": 0,
            "captured_sessions": 0,
            "supported_sessions": 0,
            "observed_sessions": 0,
            "partial_sessions": 0,
            "unknown_sessions": 0,
            "unsupported_sessions": 0,
            "not_captured_sessions": 0,
            "rate_excluded_sessions": 0,
            "rate_attempts": {"ok": 0, "failed": 0, "denominator": 0},
        },
        "approvals": {
            "window": window,
            "events": {"requests": 0, "allowed": 0, "denied": 0, "timed_out": 0,
                       "cancelled": 0, "unknown": 0},
            "unpaired_responses": 0,
            "total_sessions": 0,
            "captured_sessions": 0,
            "supported_sessions": 0,
            "observed_sessions": 0,
            "partial_sessions": 0,
            "unknown_sessions": 0,
            "unsupported_sessions": 0,
            "not_captured_sessions": 0,
        },
        "subagents": {
            "window": window,
            "events": {"total": 0, "succeeded": 0, "failed": 0, "interrupted": 0, "unknown": 0},
            "unmatched_starts": 0,
            "unmatched_stops": 0,
            "identity_gaps": 0,
            "total_sessions": 0,
            "captured_sessions": 0,
            "supported_sessions": 0,
            "observed_sessions": 0,
            "partial_sessions": 0,
            "unknown_sessions": 0,
            "unsupported_sessions": 0,
            "not_captured_sessions": 0,
        },
        "sessions": [],
        "repeated_failures": [],
    }


def _validate_timezone(timezone: str | None) -> None:
    if not timezone:
        return
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="timezone must be a valid IANA timezone.") from exc


@router.get("/overview")
def overview(
    limit: int = Query(100, ge=1, le=500),
    window: Literal["Overall", "Today", "Since Reset"] = Query("Overall"),
    timezone: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    """Control room overview for the profile selected by the Desktop host."""
    _validate_timezone(timezone)
    store, service, runtime = _core()
    try:
        with runtime.profile_connection() as conn:
            if conn is None:
                return _unavailable(window)
            return service.overview(
                conn, session_limit=limit, window=window, timezone_name=timezone
            )
    except store.UnsupportedSchemaVersion:
        raise HTTPException(
            status_code=503,
            detail="Observability data uses a newer schema; this plugin will not interpret it.",
        )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="The connected profile's session database is unavailable.")


@router.get("/sessions/{session_id}/trace")
def session_trace(
    session_id: str,
    window: Literal["Overall", "Today", "Since Reset"] = Query("Overall"),
    timezone: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    """Read one session header and its captured operational event metadata."""
    _validate_timezone(timezone)
    store, service, runtime = _core()
    try:
        with runtime.profile_connection() as conn:
            if conn is None:
                raise HTTPException(status_code=503, detail="No session database is available.")
            result = service.session_trace(
                conn, session_id, window=window, timezone_name=timezone
            )
            if result is None:
                raise HTTPException(status_code=404, detail="Session not found.")
            return result
    except store.UnsupportedSchemaVersion:
        raise HTTPException(
            status_code=503,
            detail="Observability data uses a newer schema; this plugin will not interpret it.",
        )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="The connected profile's session database is unavailable.")


@router.post("/reset")
def reset_metrics() -> dict[str, float]:
    """Advance the profile-local Since Reset cutoff without deleting any data."""
    store, _, runtime = _core()
    try:
        with runtime.profile_connection() as conn:
            if conn is None:
                raise HTTPException(status_code=503, detail="No session database is available.")
            return {"reset_cutoff": store.reset_metrics(conn)}
    except store.UnsupportedSchemaVersion:
        raise HTTPException(
            status_code=503,
            detail="Observability data uses a newer schema; this plugin will not interpret it.",
        )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="The connected profile's session database is unavailable.")


@router.post("/analyze-sessions/{session_id}/read-only")
def lock_analyze_session(
    session_id: str,
    expected_profile: str = Query(..., alias="profile", min_length=1, max_length=128),
) -> dict[str, Any]:
    """Install a durable tool-blocking policy in the requested routed profile."""
    _, _, runtime = _core()
    actual_profile = _validate_profile(runtime, expected_profile)
    policy = importlib.import_module(f"{_CORE_MODULE}.analyze_guard")
    try:
        policy.mark_read_only_session(session_id, runtime.profile_home())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid Analyze session identifier.") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Could not enforce the read-only Analyze session policy.") from exc
    return {"read_only": True, "profile": actual_profile}


@router.get("/sessions/{session_id}/calls/{call_id}/detail")
def call_detail(
    session_id: str,
    call_id: str,
    expected_profile: str | None = Query(None, alias="profile", max_length=128),
) -> dict[str, Any]:
    """Read selected call detail and bind it to the actual connected profile."""
    store, service, runtime = _core()
    actual_profile = _validate_profile(runtime, expected_profile)
    try:
        with runtime.profile_connection() as conn:
            if conn is None:
                raise HTTPException(status_code=503, detail="No session database is available.")
            result = service.call_detail(conn, session_id, call_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Call or source session not found.")
            result["profile"] = actual_profile
            return result
    except store.UnsupportedSchemaVersion:
        raise HTTPException(
            status_code=503,
            detail="Observability data uses a newer schema; this plugin will not interpret it.",
        )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="The connected profile's session database is unavailable.")
