"""Hermes observability collector plugin."""
from __future__ import annotations

from typing import Any

from .observability_core.analyze_guard import make_pre_tool_call_guard
from .observability_core.collector import register as register_collector
from .observability_core.runtime import profile_connection, profile_home


def register(ctx: Any) -> None:
    """Install the prospective observer and read-only Analyze policy hooks."""
    register_collector(ctx, profile_connection)
    ctx.register_hook("pre_tool_call", make_pre_tool_call_guard(profile_home))
