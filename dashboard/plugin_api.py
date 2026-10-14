"""Profile-local observability API mounted under /api/plugins/hermes-observability-plugin/."""
from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any

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


def _unavailable() -> dict[str, Any]:
    return {
        "available": False,
        "reason": "No readable session database is available for this connected profile.",
        "metrics": {
            "calls": {"total": 0, "ok": 0, "failed": 0, "not_executed": 0, "unknown": 0},
            "rate_calls": {"ok": 0, "failed": 0, "denominator": 0},
            "success_rate": None,
            "total_sessions": 0,
            "captured_sessions": 0,
            "observed_sessions": 0,
            "partial_sessions": 0,
            "unknown_sessions": 0,
            "excluded_sessions": 0,
            "rate_excluded_sessions": 0,
        },
        "sessions": [],
    }


@router.get("/overview")
def overview(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """Control room overview for the profile selected by the Desktop host."""
    store, service, runtime = _core()
    try:
        with runtime.profile_connection() as conn:
            if conn is None:
                return _unavailable()
            return service.overview(conn, session_limit=limit)
    except store.UnsupportedSchemaVersion:
        raise HTTPException(
            status_code=503,
            detail="Observability data uses a newer schema; this plugin will not interpret it.",
        )
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="The connected profile's session database is unavailable.")


@router.get("/sessions/{session_id}/trace")
def session_trace(session_id: str) -> dict[str, Any]:
    """Read one session header and its captured operational event metadata."""
    store, service, runtime = _core()
    try:
        with runtime.profile_connection() as conn:
            if conn is None:
                raise HTTPException(status_code=503, detail="No session database is available.")
            result = service.session_trace(conn, session_id)
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
