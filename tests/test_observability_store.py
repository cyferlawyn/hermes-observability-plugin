import sqlite3
import tempfile
import unittest
from pathlib import Path

from observability_core import store


class ObservabilityStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "state.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, title TEXT)"
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


if __name__ == "__main__":
    unittest.main()
