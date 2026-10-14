import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

from observability_core import collector, store


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "state.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, title TEXT)"
        )
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            ("new-session", 200.0, "New"),
        )
        self.conn.commit()
        store.initialize(self.conn)
        self.collector = collector.Collector(lambda: nullcontext(self.conn))
        self.collector.on_session_start(session_id="new-session")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_duplicate_post_hook_does_not_double_count_invocation(self):
        pre = {
            "session_id": "new-session", "tool_name": "files.read", "tool_call_id": "same-call"
        }
        post = {**pre, "status": "ok"}
        self.collector.on_pre_tool_call(**pre)
        self.collector.on_post_tool_call(**post)
        self.collector.on_post_tool_call(**post)

        metrics = store.metrics(self.conn)
        self.assertEqual(metrics["calls"]["total"], 1)
        self.assertEqual(metrics["calls"]["ok"], 1)

    def test_reused_tool_call_id_across_tools_matches_the_correct_pending_call(self):
        shared = {
            "session_id": "new-session", "tool_call_id": "same-call",
            "turn_id": "turn-1", "api_request_id": "request-1",
        }
        self.collector.on_pre_tool_call(**shared, tool_name="files.read")
        self.collector.on_pre_tool_call(**shared, tool_name="files.write")

        self.collector.on_post_tool_call(
            **shared, tool_name="files.write", status="error", error_type="WriteError"
        )
        self.collector.on_post_tool_call(
            **shared, tool_name="files.read", status="error", error_type="ReadError"
        )

        calls = store.session_trace(self.conn, "new-session")
        by_tool = {call["tool_name"]: call for call in calls}
        self.assertEqual(by_tool["files.read"]["error_type"], "ReadError")
        self.assertEqual(by_tool["files.write"]["error_type"], "WriteError")

    def test_reused_tool_call_id_across_requests_matches_the_correct_pending_call(self):
        common = {
            "session_id": "new-session", "tool_name": "files.read", "tool_call_id": "same-call",
            "turn_id": "turn-1",
        }
        self.collector.on_pre_tool_call(**common, api_request_id="request-1")
        self.collector.on_pre_tool_call(**common, api_request_id="request-2")

        self.collector.on_post_tool_call(
            **common, api_request_id="request-2", status="error", error_type="SecondRequestError"
        )
        self.collector.on_post_tool_call(
            **common, api_request_id="request-1", status="error", error_type="FirstRequestError"
        )

        calls = store.session_trace(self.conn, "new-session")
        by_request = {call["api_request_id"]: call for call in calls}
        self.assertEqual(by_request["request-1"]["error_type"], "FirstRequestError")
        self.assertEqual(by_request["request-2"]["error_type"], "SecondRequestError")

    def test_idless_calls_across_requests_match_the_correct_pending_call(self):
        common = {"session_id": "new-session", "tool_name": "files.read", "turn_id": "turn-1"}
        self.collector.on_pre_tool_call(**common, api_request_id="request-1")
        self.collector.on_pre_tool_call(**common, api_request_id="request-2")

        self.collector.on_post_tool_call(
            **common, api_request_id="request-2", status="error", error_type="SecondRequestError"
        )
        self.collector.on_post_tool_call(
            **common, api_request_id="request-1", status="error", error_type="FirstRequestError"
        )

        calls = store.session_trace(self.conn, "new-session")
        by_request = {call["api_request_id"]: call for call in calls}
        self.assertEqual(by_request["request-1"]["error_type"], "FirstRequestError")
        self.assertEqual(by_request["request-2"]["error_type"], "SecondRequestError")

    def test_ambiguous_idless_calls_are_not_paired_by_fifo_guess(self):
        common = {
            "session_id": "new-session", "tool_name": "files.read", "turn_id": "turn-1",
            "api_request_id": "request-1",
        }
        self.collector.on_pre_tool_call(**common)
        self.collector.on_pre_tool_call(**common)
        self.collector.on_post_tool_call(**common, status="error", error_type="FirstError")
        self.collector.on_pre_tool_call(**common)
        self.collector.on_post_tool_call(**common, status="error", error_type="SecondError")

        calls = store.session_trace(self.conn, "new-session")
        self.assertFalse(any(call["capture_state"] == "paired" for call in calls))
        self.assertEqual(sum(call["capture_state"] == "start_only" for call in calls), 3)
        self.assertEqual(sum(call["capture_state"] == "post_only" for call in calls), 2)

    def test_ambiguous_idless_quarantine_ends_at_session_finalize(self):
        common = {
            "session_id": "new-session", "tool_name": "files.read", "turn_id": "turn-1",
            "api_request_id": "request-1",
        }
        self.collector.on_pre_tool_call(**common)
        self.collector.on_pre_tool_call(**common)
        self.collector.on_post_tool_call(**common, status="error", error_type="AmbiguousError")

        self.collector.on_session_finalize(session_id="new-session")
        self.collector.on_session_start(session_id="new-session")
        self.collector.on_pre_tool_call(**common)
        self.collector.on_post_tool_call(**common, status="error", error_type="FreshError")

        calls = store.session_trace(self.conn, "new-session")
        self.assertEqual(sum(call["capture_state"] == "paired" for call in calls), 1)
        self.assertEqual(
            sum(call["error_type"] == "FreshError" and call["capture_state"] == "paired" for call in calls),
            1,
        )

    def test_post_only_duplicate_dedup_includes_exact_tool_name(self):
        shared = {
            "status": "error", "tool_call_id": "same-call",
            "turn_id": "turn-1", "api_request_id": "request-1",
        }
        read_id = store.record_post_only_call(
            self.conn, "new-session", "files.read", error_type="ReadError", **shared
        )
        write_id = store.record_post_only_call(
            self.conn, "new-session", "files.write", error_type="WriteError", **shared
        )
        duplicate_write_id = store.record_post_only_call(
            self.conn, "new-session", "files.write", error_type="WriteError", **shared
        )

        self.assertNotEqual(read_id, write_id)
        self.assertEqual(write_id, duplicate_write_id)
        calls = store.session_trace(self.conn, "new-session")
        self.assertEqual({call["tool_name"] for call in calls}, {"files.read", "files.write"})
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
