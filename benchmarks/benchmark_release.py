"""Synthetic, reproducible 1.0 release benchmark for observer hooks and read models.

Run from the repository root with:
    python3 benchmarks/benchmark_release.py

All databases are temporary and synthetic. This measures hook/query code paths, not
provider generation, actual Hermes turn latency, Electron paint, or subjective impact.
"""
from __future__ import annotations

import json
import math
import os
import platform
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from statistics import median
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability_core import collector, runtime, service, store  # noqa: E402

SESSION_COUNT = 80
CALL_COUNT = 2303
PROVIDER_ATTEMPTS = 133
TOOL_NAMES = [
    "terminal", "web_search", "browser.open", "files.read", "files.write",
    "mcp__calendar__search_events", "mcp__drive__get_file", "mcp__browser__open",
    "mcp__files__read", "mcp__files__write", "mcp__mail__search", "mcp__mail__send",
    "mcp__calendar__create_event", "mcp__calendar__get_event", "mcp__drive__list_files",
    "mcp__drive__search", "mcp__browser__navigate", "mcp__browser__click",
    "mcp__browser__screenshot", "mcp__filesystem__read_file", "mcp__filesystem__write_file",
    "mcp__github__search_code", "mcp__github__create_issue", "mcp__slack__search_messages",
    "mcp__slack__post_message",
]


def percentile(samples: list[float], percent: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(percent * len(ordered)) - 1)]


def summarize(samples_ns: list[int]) -> dict[str, float]:
    samples_ms = [sample / 1_000_000 for sample in samples_ns]
    return {
        "p50_ms": round(median(samples_ms), 4),
        "p95_ms": round(percentile(samples_ms, 0.95), 4),
        "samples": len(samples_ms),
    }


def measure(operation: Callable[[int], object], *, iterations: int) -> dict[str, float]:
    for index in range(5):
        operation(-index - 1)
    samples: list[int] = []
    for index in range(iterations):
        start = time.perf_counter_ns()
        operation(index)
        samples.append(time.perf_counter_ns() - start)
    return summarize(samples)


def make_database(path: Path) -> tuple[sqlite3.Connection, list[str]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, title TEXT)"
    )
    base_time = 1_750_000_000.0
    session_ids = [f"bench-session-{index:03d}" for index in range(SESSION_COUNT)]
    conn.executemany(
        "INSERT INTO sessions (id, started_at, ended_at, title) VALUES (?, ?, ?, ?)",
        [
            (session_id, base_time + index * 60, base_time + index * 60 + 360,
             f"Synthetic observability session {index:03d}")
            for index, session_id in enumerate(session_ids)
        ],
    )
    conn.commit()
    store.initialize(conn)
    for session_id in session_ids:
        store.register_session(conn, session_id, captured_at=base_time)
        store.register_provider_family(conn, session_id, store.OBSERVER_SCHEMA_VERSION)
        store.register_approval_family(conn, session_id, store.OBSERVER_SCHEMA_VERSION)
        store.register_subagent_family(conn, session_id, store.OBSERVER_SCHEMA_VERSION)

    for index in range(CALL_COUNT):
        session_id = session_ids[index % SESSION_COUNT]
        tool_name = TOOL_NAMES[(index * 7) % len(TOOL_NAMES)]
        start = base_time + index * 0.05
        call_id = store.begin_tool_call(
            conn, session_id, tool_name, started_at=start, tool_call_id=f"call-{index:05d}"
        )
        if call_id is None:
            raise RuntimeError("synthetic session was not registered")
        if index % 97 == 0:
            continue  # leave an explicitly unknown/incomplete lifecycle
        if index % 29 == 0:
            store.complete_tool_call(
                conn, call_id, "error", completed_at=start + 0.12,
                duration_ms=120, error_type="BenchmarkError", error_code="E_SYNTHETIC",
                error_message="synthetic deterministic failure",
            )
        elif index % 137 == 0:
            store.complete_tool_call(conn, call_id, "blocked", completed_at=start + 0.03)
        else:
            store.complete_tool_call(conn, call_id, "ok", completed_at=start + 0.02, duration_ms=20)

    for index in range(PROVIDER_ATTEMPTS):
        session_id = session_ids[index % SESSION_COUNT]
        start = base_time + index * 0.8
        attempt_id = store.begin_provider_attempt(
            conn, session_id, task_id=f"task-{index // 3}", turn_id=f"turn-{index}",
            api_request_id=f"request-{index}", api_call_count=1 + index % 3,
            provider="synthetic-provider", model="synthetic-model", started_at=start,
        )
        if attempt_id is None:
            raise RuntimeError("synthetic provider session was not registered")
        outcome = "failed" if index % 19 == 0 else "ok"
        store.complete_provider_attempt(
            conn, attempt_id, {"ended_at": start + 0.25, "api_duration": 0.25,
                               "retry_count": int(index % 19 == 0), "retryable": index % 19 == 0},
            outcome=outcome,
        )

    for index in range(16):
        session_id = session_ids[index % SESSION_COUNT]
        request_id = store.begin_approval_request(
            conn, session_id, surface="synthetic", requested_at=base_time + index * 10
        )
        if request_id is not None:
            store.complete_approval_request(
                conn, request_id, choice="once", decided_at=base_time + index * 10 + 0.1
            )

    for index in range(8):
        parent_id = session_ids[index % SESSION_COUNT]
        child_id = store.begin_subagent(
            conn, parent_id, parent_session_id=parent_id,
            child_session_id=f"bench-child-{index:02d}", child_role="researcher",
            child_goal="Synthetic benchmark child task", started_at=base_time + index * 15,
        )
        if child_id is not None:
            store.complete_subagent(
                conn, child_id, {"child_status": "success", "stopped_at": base_time + index * 15 + 4,
                                 "telemetry_schema_version": store.OBSERVER_SCHEMA_VERSION}
            )
    return conn, session_ids


def benchmark_observer_turns(db_path: Path) -> dict[str, object]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    setup = sqlite3.connect(db_path)
    setup.row_factory = sqlite3.Row
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, title TEXT)"
    )
    setup.execute(
        "INSERT INTO sessions (id, started_at, title) VALUES ('hook-benchmark', ?, 'Synthetic hook benchmark')",
        (time.time(),),
    )
    setup.commit()
    store.initialize(setup)
    setup.close()

    original_profile_home = runtime.profile_home
    runtime.profile_home = lambda: db_path.parent
    try:
        hooks = collector.Collector(runtime.profile_connection)
        hooks.on_session_start(
            session_id="hook-benchmark", telemetry_schema_version=store.OBSERVER_SCHEMA_VERSION
        )
        calls_per_turn = 10

        def disabled_turn(_: int) -> None:
            for _ in range(calls_per_turn):
                pass  # equivalent no-op observer slot; tool execution is deliberately excluded

        def enabled_turn(index: int) -> None:
            for call_index in range(calls_per_turn):
                tool_call_id = f"turn-{index}-call-{call_index}"
                common = {
                    "session_id": "hook-benchmark", "tool_name": "mcp__files__read",
                    "tool_call_id": tool_call_id, "turn_id": f"turn-{index}",
                    "telemetry_schema_version": store.OBSERVER_SCHEMA_VERSION,
                }
                hooks.on_pre_tool_call(**common)
                hooks.on_post_tool_call(**common, status="ok", duration_ms=1)

        disabled = measure(disabled_turn, iterations=200)
        enabled = measure(enabled_turn, iterations=200)
        with runtime.profile_connection() as conn:
            storage = {
                "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
                "synchronous": conn.execute("PRAGMA synchronous").fetchone()[0],
            } if conn is not None else {}
        return {"calls_per_simulated_turn": calls_per_turn, "observer_off": disabled,
                "observer_on": enabled, "storage_connection": storage}
    finally:
        runtime._close_thread_connection()
        runtime.profile_home = original_profile_home


def cpu_model() -> str:
    cpu_info = Path("/proc/cpuinfo")
    if cpu_info.is_file():
        for line in cpu_info.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def main() -> None:
    scratch = Path(os.environ.get("TMPDIR", Path.home() / ".hermes" / "cache" / "scratch"))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="observability-v1-bench-", dir=scratch) as temp_dir:
        temp = Path(temp_dir)
        hook_result = benchmark_observer_turns(temp / "hook-profile" / "state.db")
        conn, session_ids = make_database(temp / "representative-state.db")
        overview = measure(
            lambda _: service.overview(conn, session_limit=100, window="Overall"),
            iterations=60,
        )
        trace = measure(
            lambda _: service.session_trace(conn, session_ids[0], window="Overall"),
            iterations=60,
        )
        counts = {
            "sessions": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "tool_calls": conn.execute("SELECT COUNT(*) FROM hermes_observability_calls").fetchone()[0],
            "provider_attempts": conn.execute("SELECT COUNT(*) FROM hermes_observability_provider_attempts").fetchone()[0],
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "synchronous": conn.execute("PRAGMA synchronous").fetchone()[0],
        }
        conn.close()
        output = {
            "environment": {
                "platform": platform.platform(), "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version, "cpu_model": cpu_model(),
                "logical_cpus": os.cpu_count(),
            },
            "dataset": counts,
            "observer_turn": hook_result,
            "overview_query": overview,
            "selected_session_trace": trace,
            "limitations": [
                "Synthetic no-op tool turns; excludes model/provider generation and real tool execution.",
                "Overview/trace p50/p95 include Python service, SQLite query, and aggregation work; not Electron paint or browser DOM layout.",
                "Use these measurements as release evidence, not a hardware-independent latency guarantee.",
            ],
        }
        print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
