import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from observability_core import store


class ObservabilityStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "state.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, title TEXT)"
        )
        self.conn.executemany(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            [("s1", 100.0, "First"), ("s2", 200.0, "Second")],
        )
        self.conn.commit()
        store.initialize(self.conn)
        store.register_session(self.conn, "s1", captured_at=110.0)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_metrics_count_only_explicit_executed_outcomes(self):
        ok_id = store.begin_tool_call(
            self.conn, "s1", "files.read", started_at=120.0, tool_call_id="call-1"
        )
        store.complete_tool_call(self.conn, ok_id, "ok", completed_at=121.0, duration_ms=1000)

        failed_id = store.begin_tool_call(
            self.conn, "s1", "mcp:browser.open", started_at=122.0, tool_call_id="call-2"
        )
        store.complete_tool_call(
            self.conn, failed_id, "error", completed_at=123.0, duration_ms=1000,
            error_type="TimeoutError", error_message="upstream timed out"
        )

        blocked_id = store.begin_tool_call(
            self.conn, "s1", "terminal", started_at=124.0, tool_call_id="call-3"
        )
        store.complete_tool_call(self.conn, blocked_id, "blocked", completed_at=125.0)

        store.begin_tool_call(self.conn, "s1", "web.search", started_at=126.0, tool_call_id="call-4")

        metrics = store.metrics(self.conn)
        self.assertEqual(metrics["calls"], {
            "total": 4, "ok": 1, "failed": 1, "not_executed": 1, "unknown": 1
        })
        self.assertEqual(metrics["rate_calls"], {"ok": 0, "failed": 0, "denominator": 0})
        self.assertIsNone(metrics["success_rate"])
        self.assertEqual(metrics["captured_sessions"], 1)
        self.assertEqual(metrics["excluded_sessions"], 1)
        self.assertEqual(metrics["rate_excluded_sessions"], 2)

        trace = store.session_trace(self.conn, "s1")
        self.assertEqual(len(trace), 4)
        self.assertEqual(trace[1]["error_type"], "TimeoutError")
        self.assertNotIn("args", trace[0])
        self.assertNotIn("result", trace[0])

    def test_success_rate_uses_only_observed_sessions_and_reports_exclusions(self):
        store.begin_tool_call(self.conn, "s1", "files.read", started_at=120.0)
        store.register_session(self.conn, "s2", captured_at=210.0)
        success_id = store.begin_tool_call(self.conn, "s2", "files.read", started_at=220.0)
        store.complete_tool_call(self.conn, success_id, "ok", completed_at=221.0)
        failure_id = store.begin_tool_call(self.conn, "s2", "mcp:browser.open", started_at=222.0)
        store.complete_tool_call(self.conn, failure_id, "error", completed_at=223.0)

        metrics = store.metrics(self.conn)

        self.assertEqual(metrics["calls"], {
            "total": 3, "ok": 1, "failed": 1, "not_executed": 0, "unknown": 1
        })
        self.assertEqual(metrics["rate_calls"], {"ok": 1, "failed": 1, "denominator": 2})
        self.assertEqual(metrics["success_rate"], 0.5)
        self.assertEqual(metrics["observed_sessions"], 1)
        self.assertEqual(metrics["partial_sessions"], 1)
        self.assertEqual(metrics["excluded_sessions"], 0)
        self.assertEqual(metrics["rate_excluded_sessions"], 1)

    def test_source_session_deletion_cascades_telemetry(self):
        call_id = store.begin_tool_call(self.conn, "s1", "files.read", started_at=120.0)
        store.complete_tool_call(self.conn, call_id, "ok", completed_at=121.0)

        self.conn.execute("DELETE FROM sessions WHERE id = ?", ("s1",))
        self.conn.commit()

        self.assertEqual(store.session_trace(self.conn, "s1"), [])
        self.assertEqual(store.metrics(self.conn)["captured_sessions"], 0)

    def test_reset_cutoff_filters_whole_sessions_and_repeated_resets_keep_data(self):
        store.register_session(self.conn, "s2", captured_at=210.0)
        before_id = store.begin_tool_call(self.conn, "s1", "files.read", started_at=140.0)
        assert before_id is not None
        store.complete_tool_call(self.conn, before_id, "ok", completed_at=145.0)
        after_id = store.begin_tool_call(self.conn, "s2", "files.read", started_at=220.0)
        assert after_id is not None
        store.complete_tool_call(self.conn, after_id, "error", completed_at=230.0)

        first_cutoff = store.reset_metrics(self.conn, reset_at=200.0)
        first = store.metrics(self.conn, window="Since Reset", now=250.0)
        self.assertEqual(first_cutoff, 200.0)
        self.assertEqual(first["calls"], {
            "total": 1, "ok": 0, "failed": 1, "not_executed": 0, "unknown": 0
        })
        self.assertEqual(first["included_sessions"], 1)
        self.assertEqual(first["rate_included_sessions"], 1)
        self.assertEqual(first["rate_excluded_sessions"], 0)

        second_cutoff = store.reset_metrics(self.conn, reset_at=240.0)
        second = store.metrics(self.conn, window="Since Reset", now=250.0)
        self.assertGreater(second_cutoff, first_cutoff)
        self.assertEqual(second["included_sessions"], 0)
        self.assertEqual(second["calls"]["total"], 0)
        self.assertEqual(store.metrics(self.conn, window="Overall")["calls"]["total"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hermes_observability_calls").fetchone()[0], 2)

    def test_since_reset_without_a_reset_includes_all_available_sessions(self):
        overall = store.metrics(self.conn, window="Overall")
        since_reset = store.metrics(self.conn, window="Since Reset")

        self.assertEqual(since_reset["reset_cutoff"], None)
        self.assertEqual(since_reset["included_sessions"], overall["included_sessions"])
        self.assertEqual(since_reset["calls"], overall["calls"])

    def test_today_uses_local_day_and_excludes_sessions_that_cross_its_start(self):
        zone = ZoneInfo("Europe/Berlin")
        yesterday_end = datetime(2026, 10, 13, 23, 59, 59, tzinfo=zone).timestamp()
        today_start = datetime(2026, 10, 14, 0, 0, 0, tzinfo=zone).timestamp()
        now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=zone).timestamp()
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            ("yesterday", yesterday_end, "Cross-boundary"),
        )
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            ("today", today_start, "Today"),
        )
        self.conn.commit()
        store.register_session(self.conn, "yesterday", captured_at=yesterday_end)
        store.register_session(self.conn, "today", captured_at=today_start)
        old_call = store.begin_tool_call(self.conn, "yesterday", "files.read", started_at=today_start + 1)
        assert old_call is not None
        store.complete_tool_call(self.conn, old_call, "ok", completed_at=today_start + 2)
        today_call = store.begin_tool_call(self.conn, "today", "files.read", started_at=today_start + 3)
        assert today_call is not None
        store.complete_tool_call(self.conn, today_call, "ok", completed_at=today_start + 4)

        metrics = store.metrics(
            self.conn, window="Today", now=now, timezone_name="Europe/Berlin"
        )
        sessions = store.list_sessions(
            self.conn, window="Today", now=now, timezone_name="Europe/Berlin"
        )

        self.assertEqual(metrics["included_sessions"], 1)
        self.assertEqual(metrics["calls"]["total"], 1)
        self.assertEqual(metrics["success_rate"], 1.0)
        self.assertEqual([item["id"] for item in sessions], ["today"])

    def test_today_coverage_counts_and_rates_use_only_eligible_sessions(self):
        today_start = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc).timestamp()
        self.conn.executemany(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            [
                ("today-observed", today_start + 1, "Observed"),
                ("today-partial", today_start + 2, "Partial"),
                ("today-uncaptured", today_start + 3, "Uncaptured"),
            ],
        )
        self.conn.commit()
        store.register_session(self.conn, "today-observed", captured_at=today_start + 1)
        store.register_session(self.conn, "today-partial", captured_at=today_start + 2)
        ok_id = store.begin_tool_call(self.conn, "today-observed", "files.read", started_at=today_start + 4)
        assert ok_id is not None
        store.complete_tool_call(self.conn, ok_id, "ok", completed_at=today_start + 5)
        store.begin_tool_call(self.conn, "today-partial", "files.read", started_at=today_start + 6)

        metrics = store.metrics(self.conn, window="Today", now=now, timezone_name="UTC")

        self.assertEqual(metrics["included_sessions"], 3)
        self.assertEqual(metrics["observed_sessions"], 1)
        self.assertEqual(metrics["partial_sessions"], 1)
        self.assertEqual(metrics["excluded_sessions"], 1)
        self.assertEqual(metrics["rate_excluded_sessions"], 2)
        self.assertEqual(metrics["rate_calls"], {"ok": 1, "failed": 0, "denominator": 1})
        self.assertEqual(metrics["calls"]["total"], 2)
        self.assertEqual(metrics["success_rate"], 1.0)

    def test_today_can_use_the_desktop_users_timezone(self):
        before_local_midnight = datetime(2026, 10, 14, 6, 59, tzinfo=timezone.utc).timestamp()
        local_midnight = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc).timestamp()
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc).timestamp()
        self.conn.executemany(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            [
                ("before-la-day", before_local_midnight, "Before local midnight"),
                ("at-la-day", local_midnight, "At local midnight"),
            ],
        )
        self.conn.commit()

        sessions = store.list_sessions(
            self.conn, window="Today", now=now, timezone_name="America/Los_Angeles"
        )

        self.assertEqual([item["id"] for item in sessions], ["at-la-day"])

    def test_today_bounds_follow_daylight_saving_transitions(self):
        from zoneinfo import ZoneInfo

        spring_now = datetime(2026, 3, 29, 12, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
        fall_now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()

        spring_start, spring_end = store._today_bounds(spring_now, "Europe/Berlin")
        fall_start, fall_end = store._today_bounds(fall_now, "Europe/Berlin")

        self.assertEqual(spring_end - spring_start, 23 * 60 * 60)
        self.assertEqual(fall_end - fall_start, 25 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
