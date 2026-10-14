import sqlite3
import tempfile
import unittest
from pathlib import Path

from observability_core import service, store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "state.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, title TEXT)"
        )
        self.conn.executemany(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            [("captured", 100.0, "Captured"), ("old", 50.0, "Not captured")],
        )
        self.conn.commit()
        store.initialize(self.conn)
        store.register_session(self.conn, "captured", captured_at=110.0)
        call_id = store.begin_tool_call(
            self.conn, "captured", "mcp:browser.search", started_at=120.0, tool_call_id="t1"
        )
        store.complete_tool_call(
            self.conn, call_id, "error", completed_at=121.0, error_type="TimeoutError",
            error_message="request timed out"
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_overview_contains_repeated_failure_findings_for_call_drilldown(self):
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            ("captured-2", 200.0, "Second captured"),
        )
        self.conn.commit()
        store.register_session(self.conn, "captured-2", captured_at=210.0)
        call_id = store.begin_tool_call(
            self.conn, "captured-2", "mcp__browser__search", started_at=220.0, tool_call_id="t2"
        )
        store.complete_tool_call(
            self.conn, call_id, "error", completed_at=221.0,
            error_type="TimeoutError", error_message="request timed out"
        )
        self.conn.execute(
            "UPDATE hermes_observability_calls SET tool_name = ? WHERE session_id = ?",
            ("mcp__browser__search", "captured"),
        )
        self.conn.commit()

        self.conn.executemany(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            [(f"recent-{index}", 1000.0 + index, f"Recent {index}") for index in range(501)],
        )
        self.conn.commit()
        result = service.overview(self.conn, session_limit=1)

        [finding] = result["repeated_failures"]
        self.assertEqual(finding["tool_name"], "mcp__browser__search")
        self.assertEqual(finding["affected_sessions"], 2)
        self.assertEqual({item["session_id"] for item in finding["examples"]}, {"captured", "captured-2"})
        self.assertNotIn("captured", [row["id"] for row in result["sessions"]])
        old_example_trace = service.session_trace(self.conn, "captured")
        self.assertEqual(old_example_trace["session"]["id"], "captured")
        self.assertEqual(old_example_trace["calls"][0]["tool_name"], "mcp__browser__search")

    def test_overview_labels_uncaptured_sessions_and_reports_telemetry_counts(self):
        result = service.overview(self.conn)

        self.assertEqual(result["metrics"]["calls"]["failed"], 1)
        by_id = {row["id"]: row for row in result["sessions"]}
        self.assertEqual(by_id["captured"]["coverage_state"], "observed_no_known_gaps")
        self.assertEqual(by_id["old"]["coverage_state"], "not_captured")
        self.assertEqual(result["observer_schema_version"], store.OBSERVER_SCHEMA_VERSION)

    def test_trace_response_contains_only_captured_observer_events(self):
        result = service.session_trace(self.conn, "captured")

        self.assertEqual(result["session"]["id"], "captured")
        self.assertEqual(result["calls"][0]["error_type"], "TimeoutError")
        self.assertNotIn("reasoning", result)
        self.assertNotIn("request", result)
        self.assertNotIn("response", result)
        self.assertNotIn("args", result["calls"][0])

    def test_trace_response_is_none_for_unknown_session(self):
        self.assertIsNone(service.session_trace(self.conn, "missing"))

    def test_selected_window_filters_overview_metrics_sessions_and_session_trace(self):
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            ("after-reset", 200.0, "After reset"),
        )
        self.conn.commit()
        store.register_session(self.conn, "after-reset", captured_at=210.0)
        call_id = store.begin_tool_call(self.conn, "after-reset", "files.read", started_at=220.0)
        assert call_id is not None
        store.complete_tool_call(self.conn, call_id, "ok", completed_at=221.0)
        store.reset_metrics(self.conn, reset_at=150.0)

        result = service.overview(self.conn, window="Since Reset")

        self.assertEqual(result["window"], "Since Reset")
        self.assertEqual([row["id"] for row in result["sessions"]], ["after-reset"])
        self.assertEqual(result["metrics"]["included_sessions"], 1)
        self.assertEqual(result["metrics"]["calls"]["total"], 1)
        self.assertIsNone(service.session_trace(self.conn, "captured", window="Since Reset"))
        self.assertIsNotNone(service.session_trace(self.conn, "after-reset", window="Since Reset"))


if __name__ == "__main__":
    unittest.main()
