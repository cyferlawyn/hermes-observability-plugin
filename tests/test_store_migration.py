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
        self.assertEqual(conn.execute("SELECT value FROM hermes_observability_meta WHERE key = 'schema_version'").fetchone()[0], "3")
        self.assertEqual(conn.execute("SELECT error_message FROM hermes_observability_calls WHERE id = 'call1'").fetchone()[0], "disk error")
        conn.close()
    def test_schema_v2_creates_provider_family_tables_without_changing_call_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL)")
        conn.execute("INSERT INTO sessions VALUES ('s1', 100.0)")
        store.initialize(conn)
        store.register_session(conn, "s1")
        call_id = store.begin_tool_call(conn, "s1", "files.read", started_at=101.0)
        assert call_id is not None
        store.complete_tool_call(conn, call_id, "ok", completed_at=102.0)
        conn.execute("DROP TABLE hermes_observability_provider_attempts")
        conn.execute("DROP TABLE hermes_observability_family_coverage")
        conn.execute("UPDATE hermes_observability_meta SET value = '2' WHERE key = 'schema_version'")
        conn.commit()

        store.initialize(conn)

        self.assertEqual(
            conn.execute("SELECT outcome FROM hermes_observability_calls WHERE id = ?", (call_id,)).fetchone()[0],
            "ok",
        )
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertIn("hermes_observability_provider_attempts", tables)
        self.assertIn("hermes_observability_family_coverage", tables)
        self.assertEqual(
            conn.execute("SELECT value FROM hermes_observability_meta WHERE key = 'schema_version'").fetchone()[0],
            "3",
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
