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
        self.assertEqual(
            [item["tool_name"] for item in result["tool_breakdown"]],
            ["mcp__browser__search"],
        )
        self.assertNotIn("captured", [row["id"] for row in result["sessions"]])
        old_example_trace = service.session_trace(self.conn, "captured")
        self.assertEqual(old_example_trace["session"]["id"], "captured")
        self.assertEqual(old_example_trace["calls"][0]["tool_name"], "mcp__browser__search")

    def test_overview_attaches_only_explicit_exact_tool_documentation_links(self):
        self.conn.execute(
            "UPDATE hermes_observability_calls SET tool_name = ? WHERE session_id = ?",
            ("mcp__browser__search", "captured"),
        )
        second_id = store.begin_tool_call(
            self.conn, "captured", "terminal", started_at=124.0, tool_call_id="terminal-1"
        )
        assert second_id is not None
        store.complete_tool_call(self.conn, second_id, "ok", completed_at=125.0)
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES ('captured-2', 200.0, 'Second')"
        )
        self.conn.commit()
        store.register_session(self.conn, "captured-2", captured_at=210.0)
        repeated_id = store.begin_tool_call(
            self.conn, "captured-2", "mcp__browser__search", started_at=220.0, tool_call_id="browser-2"
        )
        assert repeated_id is not None
        store.complete_tool_call(
            self.conn, repeated_id, "error", completed_at=221.0,
            error_type="TimeoutError", error_message="request timed out"
        )

        result = service.overview(self.conn, documentation_links={
            "mcp__browser__search": "https://docs.example.invalid/search",
            "mcp__browser": "https://docs.example.invalid/near-miss",
        })

        by_tool = {row["tool_name"]: row for row in result["tool_breakdown"]}
        self.assertEqual(by_tool["mcp__browser__search"]["documentation_url"], "https://docs.example.invalid/search")
        self.assertNotIn("documentation_url", by_tool["terminal"])
        [finding] = result["repeated_failures"]
        self.assertEqual(finding["documentation_url"], "https://docs.example.invalid/search")

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

    def test_trace_timeline_interleaves_visible_turns_with_tool_calls(self):
        self.conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, "
            "role TEXT NOT NULL, content TEXT, timestamp REAL, tool_calls TEXT, reasoning TEXT)"
        )
        self.conn.executemany(
            "INSERT INTO messages (id, session_id, role, content, timestamp, tool_calls, reasoning) "
            "VALUES (?, 'captured', ?, ?, ?, ?, ?)",
            [
                (1, "user", "Read the file", 119.0, None, "hidden user reasoning"),
                (2, "assistant", "", 120.0, '[{"id":"source-tool-call"}]', "hidden tool-call reasoning"),
                (3, "assistant", "I inspected the file.", 122.0, None, "hidden assistant reasoning"),
            ],
        )
        self.conn.commit()

        result = service.session_trace(self.conn, "captured")
        assert result is not None

        self.assertEqual([item["kind"] for item in result["timeline"]], [
            "message", "message", "tool_call", "message"
        ])
        self.assertEqual([item.get("role") or item.get("tool_name") for item in result["timeline"]], [
            "user", "assistant", "mcp:browser.search", "assistant"
        ])
        self.assertEqual(result["timeline"][0]["content"], "Read the file")
        self.assertTrue(result["timeline"][1]["has_tool_calls"])
        self.assertEqual(result["timeline"][1]["content"], "")
        self.assertNotIn("source-tool-call", str(result["timeline"]))
        self.assertNotIn("reasoning", str(result["timeline"]))

    def test_trace_timeline_is_available_without_a_messages_table(self):
        result = service.session_trace(self.conn, "captured")
        assert result is not None

        self.assertEqual([item["kind"] for item in result["timeline"]], ["tool_call"])

    def test_trace_timeline_merges_provider_attempts_and_keeps_time_order(self):
        provider_id = store.begin_provider_attempt(
            self.conn, "captured", provider="openai", model="gpt-test", started_at=119.5
        )
        self.assertIsNotNone(provider_id)
        assert provider_id is not None
        store.complete_provider_attempt(
            self.conn, provider_id, {"ended_at": 120.5, "api_duration": 1.0}, outcome="ok"
        )

        result = service.session_trace(self.conn, "captured")
        assert result is not None

        self.assertEqual([item["kind"] for item in result["timeline"]], [
            "provider_attempt", "tool_call"
        ])
        self.assertEqual(result["timeline"][0]["provider"], "openai")

    def test_trace_timeline_merges_approval_and_subagent_events(self):
        approval_id = store.begin_approval_request(
            self.conn, "captured", surface="gateway", requested_at=121.0
        )
        assert approval_id is not None
        self.assertTrue(store.complete_approval_request(
            self.conn, approval_id, choice="once", decided_at=121.5
        ))
        subagent_id = store.begin_subagent(
            self.conn, "captured", parent_session_id="captured", child_session_id="child",
            child_role="researcher", child_goal="Find the cause", started_at=122.0,
        )
        assert subagent_id is not None
        self.assertTrue(store.complete_subagent(
            self.conn, subagent_id,
            {"child_status": "success", "stopped_at": 123.0,
             "telemetry_schema_version": store.OBSERVER_SCHEMA_VERSION},
        ))

        result = service.session_trace(self.conn, "captured")

        assert result is not None
        self.assertEqual([item["kind"] for item in result["timeline"]], [
            "tool_call", "approval", "subagent"
        ])
        self.assertEqual(result["timeline"][1]["outcome"], "allowed")
        self.assertEqual(result["timeline"][2]["child_goal"], "Find the cause")

    def test_uncaptured_session_messages_are_not_backfilled_into_trace(self):
        self.conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, "
            "role TEXT NOT NULL, content TEXT, timestamp REAL)"
        )
        self.conn.execute(
            "INSERT INTO messages (id, session_id, role, content, timestamp) "
            "VALUES (1, 'old', 'user', 'historic visible message', 60.0)"
        )
        self.conn.commit()

        result = service.session_trace(self.conn, "old")

        assert result is not None
        self.assertEqual(result["session"]["coverage_state"], "not_captured")
        self.assertEqual(result["timeline"], [])

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
