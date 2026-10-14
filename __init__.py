"""Hermes observability collector plugin."""
from __future__ import annotations

from typing import Any

from .observability_core.collector import register as register_collector
from .observability_core.runtime import profile_connection


def register(ctx: Any) -> None:
    """Install the prospective, fail-open tool-call observer hooks."""
    register_collector(ctx, profile_connection)
