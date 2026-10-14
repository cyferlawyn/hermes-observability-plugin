import sqlite3
import unittest

from observability_core import store


class StoreMigrationTests(unittest.TestCase):
    def test_schema_v1_adds_failure_status_without_losing_events(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL)")
        conn.execute("INSERT INTO sessions VALUES ('s1', 100.0)")
        conn.execute("CREATE TABLE hermes_observability_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO hermes_observability_meta VALUES ('schema_version', '1')")
        conn.execute(
            "CREATE TABLE hermes_observability_sessions ("
            "session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE, "
            "captured_at REAL NOT NULL, observer_schema_version TEXT NOT NULL, "
            "coverage_state TEXT NOT NULL DEFAULT 'observed')"
        )
        conn.execute("INSERT INTO hermes_observability_sessions VALUES ('s1', 101, 'hermes.observer.v1', 'observed')")
        conn.execute(
            "CREATE TABLE hermes_observability_calls ("
            "id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, "
            "tool_name TEXT NOT NULL, task_id TEXT, turn_id TEXT, api_request_id TEXT, tool_call_id TEXT, "
            "started_at REAL, completed_at REAL, duration_ms REAL, outcome TEXT NOT NULL DEFAULT 'unknown', "
            "error_type TEXT, error_message TEXT, capture_state TEXT NOT NULL DEFAULT 'start_only')"
        )
        conn.execute(
            "INSERT INTO hermes_observability_calls "
            "(id, session_id, tool_name, outcome, error_type, error_message, capture_state) "
            "VALUES ('call1', 's1', 'files.read', 'failed', 'OSError', 'disk error', 'paired')"
        )
        conn.commit()

        store.initialize(conn)

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(hermes_observability_calls)")}
        self.assertIn("error_code", columns)
        self.assertIn("failure_status", columns)
        self.assertEqual(conn.execute("SELECT value FROM hermes_observability_meta WHERE key = 'schema_version'").fetchone()[0], "2")
        self.assertEqual(conn.execute("SELECT error_message FROM hermes_observability_calls WHERE id = 'call1'").fetchone()[0], "disk error")
        conn.close()


if __name__ == "__main__":
    unittest.main()
