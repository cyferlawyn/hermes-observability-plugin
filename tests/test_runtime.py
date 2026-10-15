import sqlite3
import tempfile
import unittest
from pathlib import Path

from observability_core import runtime
from observability_core.runtime import _configure_profile_connection


class RuntimeConnectionTests(unittest.TestCase):
    def test_profile_connection_reuses_thread_local_connection_for_same_database(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            conn = sqlite3.connect(home / "state.db")
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL)")
            conn.commit()
            conn.close()

            second_home = home / "other-profile"
            second_home.mkdir()
            other = sqlite3.connect(second_home / "state.db")
            other.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL)")
            other.commit()
            other.close()

            with patch("observability_core.runtime.profile_home", return_value=home):
                with runtime.profile_connection() as first:
                    self.assertIsNotNone(first)
                    assert first is not None
                with runtime.profile_connection() as second:
                    self.assertIsNotNone(second)
                    assert second is not None
                    self.assertIs(first, second)
                    self.assertEqual(second.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

            with patch("observability_core.runtime.profile_home", return_value=second_home):
                with runtime.profile_connection() as switched:
                    self.assertIsNotNone(switched)
                    assert switched is not None
                    self.assertIsNot(switched, first)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        first.execute("SELECT 1")
            runtime._close_thread_connection()

    def test_explicit_hermes_database_synchronous_setting_is_honored(self):
        from unittest.mock import patch
        import sys
        import types

        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(Path(directory) / "explicit-wal.db")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            config = {"database": {"synchronous": "FULL"}}
            config_module = types.ModuleType("hermes_cli.config")
            setattr(config_module, "load_config_readonly", lambda: config)
            setattr(config_module, "cfg_get", lambda cfg, section, key, default=None: cfg.get(section, {}).get(key, default))
            hermes_cli_module = types.ModuleType("hermes_cli")
            setattr(hermes_cli_module, "__path__", [])
            state_module = types.ModuleType("hermes_state_wal")
            setattr(state_module, "apply_database_pragmas", lambda db, **_kwargs: db.execute("PRAGMA synchronous=FULL"))
            with patch.dict(sys.modules, {
                "hermes_cli": hermes_cli_module,
                "hermes_cli.config": config_module,
                "hermes_state_wal": state_module,
            }):
                _configure_profile_connection(conn)
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
            conn.close()

    def test_reduces_sync_cost_only_for_wal_on_the_plugin_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            wal = sqlite3.connect(Path(directory) / "wal.db")
            wal.execute("PRAGMA journal_mode=WAL")
            wal.execute("PRAGMA synchronous=FULL")
            _configure_profile_connection(wal)
            self.assertEqual(wal.execute("PRAGMA synchronous").fetchone()[0], 1)
            self.assertEqual(wal.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(wal.execute("PRAGMA busy_timeout").fetchone()[0], 350)
            wal.close()

            delete = sqlite3.connect(Path(directory) / "delete.db")
            _configure_profile_connection(delete)
            self.assertEqual(delete.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(delete.execute("PRAGMA synchronous").fetchone()[0], 2)
            delete.close()


if __name__ == "__main__":
    unittest.main()
