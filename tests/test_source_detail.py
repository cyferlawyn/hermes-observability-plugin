import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from observability_core import service, store


class SourceDetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "state.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, title TEXT)"
        )
        self.conn.execute("INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
                          ("captured", 100.0, "Captured"))
        self.conn.execute(
            """CREATE TABLE messages (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
                timestamp REAL NOT NULL, reasoning TEXT, reasoning_content TEXT,
                reasoning_details TEXT, api_content TEXT, codex_message_items TEXT
            )"""
        )
        self.conn.commit()
        store.initialize(self.conn)
        store.register_session(self.conn, "captured", captured_at=110.0)
        self.call_id = store.begin_tool_call(
            self.conn, "captured", "files.read", started_at=120.0, tool_call_id="canonical-1"
        )
        store.complete_tool_call(
            self.conn, self.call_id, "error", completed_at=121.0,
            error_type="ReadError", error_message="permission denied"
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _insert_source_call(self, call_id="canonical-1", tool_name="files.read"):
        self.conn.executemany(
            """INSERT INTO messages
               (id, session_id, role, content, tool_call_id, tool_calls, tool_name,
                timestamp, reasoning, reasoning_content, reasoning_details, api_content,
                codex_message_items)
               VALUES (?, 'captured', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (1, "user", "Read the config file", None, None, None, 118.0,
                 "hidden user reasoning", "hidden user reasoning content", "{}",
                 "provider-only injected context", "provider-only items"),
                (2, "assistant", "I'll inspect that file.", None,
                 json.dumps([{"id": call_id, "type": "function", "function": {
                     "name": tool_name, "arguments": '{\"path\": \"config.yaml\"}'
                 }}]), None, 120.0, "do not expose this reasoning", "hidden reasoning",
                 "{\"secret\": true}", "provider request", "provider response"),
                (3, "tool", "contents: mode: 0600", call_id, None, "files.read", 121.0,
                 None, None, None, None, None),
                (4, "assistant", "The file is not readable.", None, None, None, 122.0,
                 "another hidden thought", None, None, None, None),
            ],
        )
        self.conn.commit()

    def test_call_detail_uses_exact_source_id_and_returns_visible_context_only(self):
        self._insert_source_call()
        before_call_count = self.conn.execute(
            "SELECT COUNT(*) FROM hermes_observability_calls"
        ).fetchone()[0]
        before_source = self.conn.execute(
            "SELECT id, role, content, tool_call_id, tool_calls FROM messages ORDER BY id"
        ).fetchall()

        result = service.call_detail(self.conn, "captured", self.call_id)

        after_call_count = self.conn.execute(
            "SELECT COUNT(*) FROM hermes_observability_calls"
        ).fetchone()[0]
        after_source = self.conn.execute(
            "SELECT id, role, content, tool_call_id, tool_calls FROM messages ORDER BY id"
        ).fetchall()
        self.assertEqual(before_call_count, after_call_count)
        self.assertEqual(before_source, after_source)
        self.assertTrue(result["available"])
        self.assertIn("Analyze this failed Hermes tool call", result["analyze_prompt"])
        self.assertEqual(result["source"]["status"], "matched")
        self.assertEqual(result["source"]["arguments"], {"path": "config.yaml"})
        self.assertEqual(result["source"]["result"], "contents: mode: 0600")
        self.assertEqual([item["content"] for item in result["context"]], [
            "Read the config file", "I'll inspect that file.", "The file is not readable."
        ])
        self.assertEqual(result["call"]["error_message"], "permission denied")
        serialized = json.dumps(result)
        for forbidden in ("hidden user reasoning", "do not expose", "provider-only", "provider request", "provider response"):
            self.assertNotIn(forbidden, serialized)

    def test_call_detail_reports_missing_source_without_fuzzy_matching(self):
        self._insert_source_call(call_id="different-id")

        result = service.call_detail(self.conn, "captured", self.call_id)

        self.assertFalse(result["available"])
        self.assertEqual(result["source"]["status"], "missing")
        self.assertIsNone(result["source"]["arguments"])
        self.assertIn("Source correlation: missing", result["analyze_prompt"])
        self.assertIn("Arguments: unavailable", result["analyze_prompt"])
        self.assertIn("permission denied", result["analyze_prompt"])
        self.assertIsNone(result["source"]["result"])
        self.assertEqual(result["context"], [])

    def test_call_detail_reports_mismatched_tool_name_as_missing(self):
        self._insert_source_call(tool_name="files.write")

        result = service.call_detail(self.conn, "captured", self.call_id)

        self.assertFalse(result["available"])
        self.assertEqual(result["source"]["status"], "missing")
        self.assertIsNone(result["source"]["arguments"])

    def test_call_detail_does_not_choose_between_reused_source_call_ids(self):
        self._insert_source_call()
        self.conn.execute(
            "INSERT INTO messages (id, session_id, role, content, tool_calls, timestamp) "
            "VALUES (5, 'captured', 'assistant', 'second request', ?, 123.0)",
            (json.dumps([{"id": "canonical-1", "function": {
                "name": "files.read", "arguments": '{\"path\": \"other.yaml\"}'
            }}]),),
        )
        self.conn.commit()

        result = service.call_detail(self.conn, "captured", self.call_id)

        self.assertFalse(result["available"])
        self.assertEqual(result["source"]["status"], "ambiguous")
        self.assertIsNone(result["source"]["arguments"])
        self.assertEqual(result["context"], [])

    def test_call_detail_is_unavailable_after_source_session_deletion(self):
        self._insert_source_call()
        self.conn.execute("DELETE FROM sessions WHERE id = ?", ("captured",))
        self.conn.commit()

        result = service.call_detail(self.conn, "captured", self.call_id)

        self.assertIsNone(result)

    def test_call_detail_rejects_unknown_event_or_session(self):
        self._insert_source_call()

        self.assertIsNone(service.call_detail(self.conn, "other-session", self.call_id))
        self.assertIsNone(service.call_detail(self.conn, "captured", "unknown-event"))


if __name__ == "__main__":
    unittest.main()
