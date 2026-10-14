import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from observability_core import store


class RepeatedFailureFindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "state.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, title TEXT)"
        )
        self.conn.commit()
        store.initialize(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add_session(self, session_id, started_at=100.0, *, captured=True):
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            (session_id, started_at, session_id),
        )
        self.conn.commit()
        if captured:
            store.register_session(self.conn, session_id, captured_at=started_at)

    def add_failure(
        self, session_id, *, tool="files.read", call_id=None, error_code=None, error_type=None,
        error_message=None, status="error", started_at=110.0, turn_id=None, api_request_id=None,
    ):
        call = store.begin_tool_call(
            self.conn, session_id, tool, started_at=started_at, tool_call_id=call_id,
            turn_id=turn_id, api_request_id=api_request_id,
        )
        self.assertIsNotNone(call)
        store.complete_tool_call(
            self.conn, call, status, completed_at=started_at + 1,
            error_code=error_code, error_type=error_type, error_message=error_message,
        )
        return call

    def findings(self, **kwargs):
        return store.repeated_failure_findings(self.conn, **kwargs)

    def test_structured_error_type_groups_failures_across_two_sessions(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", error_type="TimeoutError", error_message="first detail")
        self.add_failure("s2", call_id="b", error_type="TimeoutError", error_message="different detail")

        [finding] = self.findings()

        self.assertEqual(finding["tool_name"], "files.read")
        self.assertEqual(finding["match_basis"], "structured_error_type")
        self.assertEqual(finding["distinct_failed_calls"], 2)
        self.assertEqual(finding["affected_sessions"], 2)
        self.assertEqual({example["session_id"] for example in finding["examples"]}, {"s1", "s2"})
        self.assertEqual(finding["first_observed_at"], 111.0)
        self.assertEqual(finding["last_observed_at"], 111.0)
        self.assertEqual(finding["retry_count"], None)
        self.assertEqual(finding["retry_state"], "not_captured")

    def test_structured_error_code_precedes_error_type_and_message(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure(
            "s1", call_id="a", error_code="E_CONN_RESET", error_type="OSError", error_message="first"
        )
        self.add_failure(
            "s2", call_id="b", error_code="E_CONN_RESET", error_type="ConnectionError", error_message="second"
        )

        [finding] = self.findings()

        self.assertEqual(finding["match_basis"], "structured_error_code")
        self.assertEqual(finding["signature"], "E_CONN_RESET")

    def test_exact_message_masks_only_allow_listed_uuid_and_iso_timestamp(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure(
            "s1", call_id="a", error_message="Request 123 failed at 2026-10-14T21:07:50Z, id 550e8400-e29b-41d4-a716-446655440000"
        )
        self.add_failure(
            "s2", call_id="b", error_message="Request 123 failed at 2026-10-15T21:07:50Z, id 550e8400-e29b-41d4-a716-446655440001"
        )

        [finding] = self.findings()

        self.assertEqual(finding["match_basis"], "exact_message_masked_uuid_timestamp")
        self.assertEqual(finding["distinct_failed_calls"], 2)

    def test_literal_mask_placeholder_does_not_collide_with_a_masked_uuid(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", error_message="Failure <UUID>")
        self.add_failure(
            "s2", call_id="b", error_message="Failure 550e8400-e29b-41d4-a716-446655440000"
        )

        self.assertEqual(self.findings(), [])

    def test_numeric_ids_and_semantically_meaningful_values_are_not_normalized(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", error_message="Request 123 failed with HTTP status 500")
        self.add_failure("s2", call_id="b", error_message="Request 124 failed with HTTP status 500")

        self.assertEqual(self.findings(), [])

    def test_semantically_meaningful_values_and_near_matches_are_not_normalized(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", error_message="HTTP status 500")
        self.add_failure("s2", call_id="b", error_message="HTTP status 503")

        self.assertEqual(self.findings(), [])

    def test_mcp_wire_identity_keeps_server_and_tool_distinct(self):
        for session in ("s1", "s2", "s3", "s4"):
            self.add_session(session)
        self.add_failure("s1", tool="mcp__alpha__lookup", call_id="a", error_type="RemoteError")
        self.add_failure("s2", tool="mcp__alpha__lookup", call_id="b", error_type="RemoteError")
        self.add_failure("s3", tool="mcp__beta__lookup", call_id="c", error_type="RemoteError")
        self.add_failure("s4", tool="mcp__alpha__write", call_id="d", error_type="RemoteError")

        [finding] = self.findings()

        self.assertEqual(finding["identity_kind"], "mcp")
        self.assertEqual(finding["tool_name"], "mcp__alpha__lookup")
        self.assertEqual(finding["affected_sessions"], 2)

    def test_two_failures_in_one_session_do_not_become_cross_session_finding(self):
        self.add_session("s1")
        self.add_failure("s1", call_id="a", error_type="PermissionError")
        self.add_failure("s1", call_id="b", error_type="PermissionError")

        self.assertEqual(self.findings(), [])

    def test_repeated_event_rows_for_one_tool_call_id_count_once(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="same-id", error_type="RemoteError", started_at=110)
        self.add_failure("s1", call_id="same-id", error_type="RemoteError", started_at=120)
        self.add_failure("s2", call_id="other-id", error_type="RemoteError", started_at=130)

        [finding] = self.findings()

        self.assertEqual(finding["distinct_failed_calls"], 2)
        self.assertEqual(finding["affected_sessions"], 2)

    def test_reused_tool_call_id_in_distinct_turns_and_requests_counts_separately(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure(
            "s1", call_id="same-id", turn_id="turn-1", api_request_id="request-1",
            error_type="RemoteError", started_at=110,
        )
        self.add_failure(
            "s1", call_id="same-id", turn_id="turn-1", api_request_id="request-1",
            error_type="RemoteError", started_at=120,
        )
        self.add_failure(
            "s1", call_id="same-id", turn_id="turn-2", api_request_id="request-2",
            error_type="RemoteError", started_at=130,
        )
        self.add_failure(
            "s2", call_id="same-id", turn_id="turn-1", api_request_id="request-1",
            error_type="RemoteError", started_at=140,
        )

        [finding] = self.findings()

        self.assertEqual(finding["distinct_failed_calls"], 3)
        self.assertEqual(finding["affected_sessions"], 2)

    def test_raw_tool_names_are_not_trimmed_before_matching(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", tool="files.read", call_id="a", error_type="RemoteError")
        self.add_failure("s2", tool=" files.read ", call_id="b", error_type="RemoteError")

        self.assertEqual(self.findings(), [])

    def test_generic_tool_error_uses_exact_message_not_broad_error_type(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", error_type="tool_error", error_message="HTTP 500")
        self.add_failure("s2", call_id="b", error_type="tool_error", error_message="HTTP 503")

        self.assertEqual(self.findings(), [])

    def test_whitespace_padded_generic_error_type_falls_back_to_exact_message(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure(
            "s1", call_id="a", error_type=" Error ", error_message="HTTP status 500"
        )
        self.add_failure(
            "s2", call_id="b", error_type=" Error ", error_message="HTTP status 503"
        )

        self.assertEqual(self.findings(), [])

    def test_timeout_status_is_an_explicit_failure_with_timeout_count(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", status="timeout", error_type="TimeoutError")
        self.add_failure("s2", call_id="b", status="timeout", error_type="TimeoutError")

        [finding] = self.findings()

        self.assertEqual(finding["distinct_failed_calls"], 2)
        self.assertEqual(finding["timeout_count"], 2)
        self.assertEqual(finding["match_basis"], "structured_error_type")

    def test_failure_status_without_structured_error_or_message_is_not_a_signature(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", status="timeout")
        self.add_failure("s2", call_id="b", status="timeout")

        self.assertEqual(self.findings(), [])

    def test_missing_tool_identity_is_not_grouped_as_unknown(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", tool=None, call_id="a", error_type="RemoteError")
        self.add_failure("s2", tool=None, call_id="b", error_type="RemoteError")

        self.assertEqual(self.findings(), [])

    def test_whitespace_only_error_code_is_not_a_signature(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", error_code=" ")
        self.add_failure("s2", call_id="b", error_code=" ")

        self.assertEqual(self.findings(), [])

    def test_whitespace_only_error_type_is_not_a_signature(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", error_type="  ")
        self.add_failure("s2", call_id="b", error_type="  ")

        self.assertEqual(self.findings(), [])

    def test_whitespace_only_error_message_is_not_a_signature(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_failure("s1", call_id="a", error_message=" ")
        self.add_failure("s2", call_id="b", error_message=" ")

        self.assertEqual(self.findings(), [])

    def test_examples_include_a_call_from_each_affected_session(self):
        self.add_session("s1")
        self.add_session("s2")
        for index in range(6):
            self.add_failure(
                "s1", call_id=f"s1-{index}", error_type="RemoteError", started_at=110 + index
            )
        self.add_failure("s2", call_id="s2-1", error_type="RemoteError", started_at=200)

        [finding] = self.findings()

        self.assertEqual(len(finding["examples"]), 5)
        self.assertEqual({item["session_id"] for item in finding["examples"]}, {"s1", "s2"})

    def test_incomplete_population_uses_lower_bound_label(self):
        self.add_session("s1")
        self.add_session("s2")
        self.add_session("not-captured", captured=False)
        self.add_failure("s1", call_id="a", error_type="RemoteError")
        self.add_failure("s2", call_id="b", error_type="RemoteError")

        [finding] = self.findings()

        self.assertTrue(finding["at_least_n_observed"])
        self.assertEqual(finding["population_excluded_sessions"], 1)

    def test_selected_window_filters_repeated_failure_findings(self):
        today_start = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc).timestamp()
        self.add_session("today-1", started_at=today_start + 1)
        self.add_session("today-2", started_at=today_start + 2)
        self.add_session("yesterday", started_at=today_start - 1)
        self.add_failure("today-1", call_id="a", error_type="RemoteError", started_at=today_start + 3)
        self.add_failure("today-2", call_id="b", error_type="RemoteError", started_at=today_start + 4)
        self.add_failure("yesterday", call_id="c", error_type="OtherError", started_at=today_start + 5)
        store.reset_metrics(self.conn, reset_at=today_start + 2)

        [today_finding] = self.findings(window="Today", now=now, timezone_name="UTC")
        reset_findings = self.findings(window="Since Reset", now=now, timezone_name="UTC")

        self.assertEqual(today_finding["distinct_failed_calls"], 2)
        self.assertEqual(reset_findings, [])


if __name__ == "__main__":
    unittest.main()
