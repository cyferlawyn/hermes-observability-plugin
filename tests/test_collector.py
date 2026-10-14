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
        self.conn.executemany(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            [("already-open", 100.0, "Old"), ("new-session", 200.0, "New")],
        )
        self.conn.commit()
        store.initialize(self.conn)
        self.collector = collector.Collector(lambda: nullcontext(self.conn))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_only_sessions_started_after_enablement_are_captured(self):
        self.collector.on_pre_tool_call(
            session_id="already-open", tool_name="terminal", tool_call_id="old-call"
        )
        self.collector.on_post_tool_call(
            session_id="already-open", tool_name="terminal", tool_call_id="old-call", status="ok"
        )
        self.assertEqual(store.metrics(self.conn)["calls"]["total"], 0)

        self.collector.on_session_start(session_id="new-session")
        self.collector.on_pre_tool_call(
            session_id="new-session", tool_name="files.read", tool_call_id="new-call",
            args={"secret": "must not persist"}
        )
        self.collector.on_post_tool_call(
            session_id="new-session", tool_name="files.read", tool_call_id="new-call",
            status="ok", result="must not persist"
        )

        trace = store.session_trace(self.conn, "new-session")
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["outcome"], "ok")
        self.assertEqual(store.metrics(self.conn)["calls"]["total"], 1)
        self.assertNotIn("must not persist", repr(trace))


if __name__ == "__main__":
    unittest.main()
