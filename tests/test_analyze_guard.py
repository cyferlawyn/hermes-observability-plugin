import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from observability_core import analyze_guard


class AnalyzeGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_marker_descriptor_metadata_is_verified_before_bytes_written(self):
        session_id = "runtime-analyze-new-marker-owner"
        marker = analyze_guard.marker_path(session_id, self.home)
        invalid_metadata = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=os.geteuid() + 1,
            st_nlink=1,
        )
        with patch.object(analyze_guard.os, "fstat", return_value=invalid_metadata):
            with self.assertRaisesRegex(PermissionError, "ownership or permissions"):
                analyze_guard.mark_read_only_session(session_id, self.home)

        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_bytes(), b"")

    def test_new_policy_directories_sync_each_parent_entry(self):
        synced = []
        with patch.object(analyze_guard, "_sync_directory", side_effect=synced.append):
            analyze_guard.mark_read_only_session("runtime-analyze-fsync", self.home)

        policy_root = self.home / ".hermes-observability"
        marker_directory = policy_root / "analyze-read-only"
        self.assertIn(self.home, synced)
        self.assertIn(policy_root, synced)
        self.assertIn(marker_directory, synced)

    def test_existing_marker_retry_resyncs_directories_after_prior_fsync_failure(self):
        session_id = "runtime-analyze-fsync-retry"
        marker = analyze_guard.marker_path(session_id, self.home)
        failed_after_marker = False
        calls = []

        def fail_once_on_new_marker_entry(directory):
            nonlocal failed_after_marker
            marker_exists = marker.exists()
            calls.append((directory, marker_exists))
            if marker_exists and directory == marker.parent and not failed_after_marker:
                failed_after_marker = True
                raise OSError("simulated directory fsync failure")

        with patch.object(analyze_guard, "_sync_directory", side_effect=fail_once_on_new_marker_entry):
            with self.assertRaisesRegex(OSError, "simulated directory fsync failure"):
                analyze_guard.mark_read_only_session(session_id, self.home)
            self.assertTrue(marker.exists())
            self.assertTrue(analyze_guard.mark_read_only_session(session_id, self.home))

        retry_syncs = [directory for directory, marker_exists in calls if marker_exists]
        self.assertIn(self.home, retry_syncs)
        self.assertIn(self.home / ".hermes-observability", retry_syncs)
        self.assertIn(marker.parent, retry_syncs)

    def test_marked_session_tool_calls_are_blocked_across_guard_instances(self):
        session_id = "runtime-analyze-123"
        analyze_guard.mark_read_only_session(session_id, self.home)
        guard = analyze_guard.make_pre_tool_call_guard(lambda: self.home)

        for tool_name in ("write_file", "terminal", "delegate_task", "memory"):
            with self.subTest(tool_name=tool_name):
                result = guard(session_id=session_id, tool_name=tool_name)
                self.assertEqual(result["action"], "block")
                self.assertIn("read-only Analyze session", result["message"])

    def test_policy_directory_symlink_is_rejected(self):
        outside = tempfile.TemporaryDirectory()
        try:
            (self.home / ".hermes-observability").symlink_to(outside.name, target_is_directory=True)
            with self.assertRaises(PermissionError):
                analyze_guard.mark_read_only_session("runtime-analyze-symlink", self.home)
        finally:
            outside.cleanup()

    def test_unmarked_session_tool_calls_are_not_intercepted(self):
        guard = analyze_guard.make_pre_tool_call_guard(lambda: self.home)

        self.assertIsNone(guard(session_id="ordinary-session", tool_name="write_file"))

    def test_missing_session_id_is_blocked_fail_closed(self):
        guard = analyze_guard.make_pre_tool_call_guard(lambda: self.home)

        result = guard(tool_name="write_file")

        self.assertEqual(result["action"], "block")
        self.assertIn("session identity is unavailable", result["message"])

    def test_marker_owned_by_another_user_is_rejected(self):
        session_id = "runtime-analyze-owner"
        analyze_guard.mark_read_only_session(session_id, self.home)
        foreign_owner = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.geteuid() + 1, st_nlink=1)
        with patch.object(analyze_guard.os, "fstat", return_value=foreign_owner):
            with self.assertRaisesRegex(PermissionError, "ownership or permissions"):
                analyze_guard.is_read_only_session(session_id, self.home)

    def test_marker_with_group_or_other_permissions_is_rejected(self):
        session_id = "runtime-analyze-mode"
        analyze_guard.mark_read_only_session(session_id, self.home)
        analyze_guard.marker_path(session_id, self.home).chmod(0o640)

        with self.assertRaisesRegex(PermissionError, "ownership or permissions"):
            analyze_guard.is_read_only_session(session_id, self.home)

    def test_fifo_marker_is_opened_nonblocking_and_rejected(self):
        session_id = "runtime-analyze-fifo"
        analyze_guard.mark_read_only_session(session_id, self.home)
        marker = analyze_guard.marker_path(session_id, self.home)
        marker.unlink()
        os.mkfifo(marker, 0o600)
        real_open = os.open

        def require_nonblocking(path, flags, *args, **kwargs):
            if Path(path) == marker:
                self.assertTrue(flags & getattr(os, "O_NONBLOCK", 0))
            return real_open(path, flags, *args, **kwargs)

        with patch.object(analyze_guard.os, "open", side_effect=require_nonblocking):
            with self.assertRaisesRegex(PermissionError, "ownership or permissions"):
                analyze_guard.is_read_only_session(session_id, self.home)

    def test_empty_session_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "session_id must be a non-empty string"):
            analyze_guard.is_read_only_session("", self.home)

    def test_invalid_or_tampered_policy_marker_fails_closed(self):
        session_id = "runtime-analyze-456"
        analyze_guard.mark_read_only_session(session_id, self.home)
        marker = analyze_guard.marker_path(session_id, self.home)
        marker.write_text("unexpected", encoding="utf-8")
        guard = analyze_guard.make_pre_tool_call_guard(lambda: self.home)

        with self.assertLogs("observability_core.analyze_guard", level="ERROR"):
            result = guard(session_id=session_id, tool_name="write_file")

        self.assertEqual(result["action"], "block")
        self.assertIn("could not verify", result["message"])

    def test_policy_lookup_errors_fail_closed(self):
        guard = analyze_guard.make_pre_tool_call_guard(
            lambda: (_ for _ in ()).throw(PermissionError("policy directory unavailable"))
        )

        with self.assertLogs("observability_core.analyze_guard", level="ERROR"):
            result = guard(session_id="ordinary-session", tool_name="write_file")

        self.assertEqual(result["action"], "block")
        self.assertIn("could not verify", result["message"])


if __name__ == "__main__":
    unittest.main()
