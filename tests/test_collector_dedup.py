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


if __name__ == "__main__":
    unittest.main()
