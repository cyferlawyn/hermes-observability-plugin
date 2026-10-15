import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

from observability_core import collector, service, store


class ApprovalLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "state.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, title TEXT)"
        )
        self.conn.executemany(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            [("s1", 100.0, "One"), ("s2", 200.0, "Two")],
        )
        self.conn.commit()
        store.initialize(self.conn)
        self.collector = collector.Collector(lambda: nullcontext(self.conn))
        self.collector.on_session_start(session_id="s1", telemetry_schema_version="hermes.observer.v1")
        self.collector.on_session_start(session_id="s2", telemetry_schema_version="hermes.observer.v1")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_approval_hooks_capture_allowed_lifecycle_without_changing_call_metrics(self):
        class Context:
            def __init__(self):
                self.hooks = {}

            def register_hook(self, name, callback):
                self.hooks[name] = callback

        context = Context()
        collector.register(context, lambda: nullcontext(self.conn))
        context.hooks["on_session_start"](
            session_id="s1", telemetry_schema_version="hermes.observer.v1"
        )
        pre = context.hooks["pre_approval_request"]
        post = context.hooks["post_approval_response"]
        common = {
            "session_key": "s1", "turn_id": "turn-1", "tool_call_id": "tool-call-1",
            "surface": "gateway", "pattern_key": "rm_rf", "command": "secret command",
            "telemetry_schema_version": "hermes.observer.v1",
        }
        pre(**common)
        post(**common, choice="once")
        self.collector.on_pre_tool_call(
            session_id="s1", tool_name="terminal", turn_id="turn-1", tool_call_id="tool-call-1",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_post_tool_call(
            session_id="s1", tool_name="terminal", turn_id="turn-1",
            tool_call_id="tool-call-1", status="ok",
            telemetry_schema_version="hermes.observer.v1",
        )

        trace = service.session_trace(self.conn, "s1")
        assert trace is not None
        [approval] = trace["approvals"]
        call = store.session_trace(self.conn, "s1")[0]

        self.assertEqual(approval["outcome"], "allowed")
        self.assertEqual(approval["capture_state"], "paired")
        self.assertEqual(approval["tool_call_id"], "tool-call-1")
        self.assertEqual(approval["related_call_id"], call["id"])
        self.assertEqual(approval["related_call_status"], "matched")
        self.assertIsNone(approval["pattern_key"])
        self.assertEqual(approval["pattern_keys"], [])
        self.assertNotIn("secret command", repr(trace))
        self.assertEqual(call["outcome"], "ok")
        self.assertEqual(store.metrics(self.conn)["calls"]["total"], 1)
        self.assertEqual(store.metrics(self.conn)["success_rate"], 1.0)
        self.assertEqual(trace["approval_coverage"]["coverage_state"], "observed_no_known_gaps")

    def test_approval_outcomes_have_independent_counts_and_coverage(self):
        for index, choice in enumerate(("once", "deny", "timeout", "cancelled", "future-choice")):
            event = {
                "session_key": "s1", "turn_id": f"turn-{index}",
                "tool_call_id": f"tool-{index}", "surface": "gateway",
                "pattern_key": "sudo", "telemetry_schema_version": "hermes.observer.v1",
            }
            self.collector.on_pre_approval_request(**event)
            self.collector.on_post_approval_response(**event, choice=choice)

        trace = service.session_trace(self.conn, "s1")
        assert trace is not None
        self.assertEqual(
            [event["outcome"] for event in trace["approvals"]],
            ["allowed", "denied", "timed_out", "cancelled", "unknown"],
        )
        metrics = store.approval_metrics(self.conn)
        self.assertEqual(metrics["events"], {
            "requests": 5, "allowed": 1, "denied": 1, "timed_out": 1,
            "cancelled": 1, "unknown": 1,
        })
        self.assertEqual(metrics["partial_sessions"], 1)
        self.assertEqual(store.metrics(self.conn)["calls"]["total"], 0)

    def test_unmatched_events_and_unknown_schema_are_not_reported_as_zero(self):
        self.collector.on_pre_approval_request(
            session_key="s1", turn_id="request-without-response", surface="cli",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_post_approval_response(
            session_key="s1", turn_id="response-without-request", surface="gateway",
            choice="deny", telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_session_start(
            session_id="s2", telemetry_schema_version="future.observer.v99"
        )

        trace = service.session_trace(self.conn, "s1")
        assert trace is not None
        self.assertEqual(
            {event["capture_state"] for event in trace["approvals"]},
            {"request_only", "response_only"},
        )
        self.assertEqual(trace["approval_coverage"]["coverage_state"], "partial")
        metrics = store.approval_metrics(self.conn)
        self.assertEqual(metrics["unpaired_responses"], 1)
        self.assertEqual(metrics["unknown_sessions"], 1)
        self.assertEqual(metrics["observed_sessions"], 0)
        self.assertEqual(metrics["not_captured_sessions"], 0)

        self.conn.execute("DELETE FROM sessions WHERE id = 's1'")
        self.conn.commit()
        self.assertEqual(store.session_approvals(self.conn, "s1"), [])
        self.assertEqual(store.approval_metrics(self.conn)["events"]["requests"], 0)

    def test_approval_metrics_exclude_rows_from_unknown_schema_sessions(self):
        supported_event = {
            "session_key": "s1", "turn_id": "supported-turn", "tool_call_id": "supported-call",
            "surface": "gateway", "telemetry_schema_version": "hermes.observer.v1",
        }
        unknown_event = {
            "session_key": "s2", "turn_id": "unknown-turn", "tool_call_id": "unknown-call",
            "surface": "gateway", "telemetry_schema_version": "future.observer.v99",
        }
        self.collector.on_pre_approval_request(**supported_event)
        self.collector.on_post_approval_response(**supported_event, choice="once")
        self.collector.on_post_approval_response(
            session_key="s1", turn_id="supported-unmatched", surface="gateway",
            choice="deny", telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_session_start(
            session_id="s2", telemetry_schema_version="future.observer.v99"
        )
        self.collector.on_pre_approval_request(**unknown_event)
        self.collector.on_post_approval_response(**unknown_event, choice="deny")
        self.collector.on_post_approval_response(
            session_key="s2", turn_id="unknown-unmatched", surface="gateway",
            choice="deny", telemetry_schema_version="future.observer.v99",
        )

        metrics = store.approval_metrics(self.conn)
        self.assertEqual(metrics["events"]["requests"], 1)
        self.assertEqual(metrics["events"]["allowed"], 1)
        self.assertEqual(metrics["events"]["denied"], 0)
        self.assertEqual(metrics["unpaired_responses"], 1)
        self.assertEqual(metrics["supported_sessions"], 1)
        self.assertEqual(metrics["unknown_sessions"], 1)

    def test_free_text_hook_fields_are_never_persisted_as_approval_metadata(self):
        private_text = "sensitive command phrase: rm -rf /home/example"
        event = {
            "session_key": "s1", "turn_id": "privacy-turn", "tool_call_id": "privacy-call",
            "surface": private_text, "pattern_key": private_text, "pattern_keys": [private_text],
            "command": private_text, "description": private_text,
            "telemetry_schema_version": "hermes.observer.v1",
        }
        self.collector.on_pre_approval_request(**event)
        self.collector.on_post_approval_response(
            **event, choice=private_text, decided_by=private_text
        )

        [approval] = store.session_approvals(self.conn, "s1")
        self.assertNotIn(private_text, repr(approval))
        self.assertEqual(approval["surface"], "unknown")
        self.assertIsNone(approval["pattern_key"])
        self.assertEqual(approval["pattern_keys"], [])
        self.assertEqual(approval["choice"], "unknown")
        self.assertIsNone(approval["decided_by"])
        self.assertEqual(approval["outcome"], "unknown")

    def test_ambiguous_approval_requests_are_never_paired_by_arrival_order(self):
        common = {
            "session_key": "s1", "turn_id": "same-turn", "tool_call_id": "same-tool-call",
            "surface": "gateway", "pattern_key": "sudo",
            "telemetry_schema_version": "hermes.observer.v1",
        }
        self.collector.on_pre_approval_request(**common)
        self.collector.on_pre_approval_request(**common)
        self.collector.on_post_approval_response(**common, choice="deny")
        self.collector.on_post_approval_response(**common, choice="once")

        events = store.session_approvals(self.conn, "s1")
        self.assertEqual(sum(event["capture_state"] == "paired" for event in events), 0)
        self.assertEqual(sum(event["capture_state"] == "request_only" for event in events), 2)
        self.assertEqual(sum(event["capture_state"] == "response_only" for event in events), 2)
        self.assertEqual(store.approval_session_coverage(self.conn, "s1")["coverage_state"], "partial")

    def test_approval_events_without_correlation_ids_remain_unmatched(self):
        common = {
            "session_key": "s1", "surface": "cli", "pattern_key": "sudo",
            "telemetry_schema_version": "hermes.observer.v1",
        }
        self.collector.on_pre_approval_request(**common)
        self.collector.on_post_approval_response(**common, choice="once")

        events = store.session_approvals(self.conn, "s1")
        self.assertEqual(sum(event["capture_state"] == "paired" for event in events), 0)
        self.assertEqual(sum(event["capture_state"] == "request_only" for event in events), 1)
        self.assertEqual(sum(event["capture_state"] == "response_only" for event in events), 1)

    def test_reused_approval_key_quarantines_late_duplicate_response(self):
        common = {
            "session_key": "s1", "turn_id": "reused-turn", "tool_call_id": "reused-tool-call",
            "surface": "gateway", "pattern_key": "sudo",
            "telemetry_schema_version": "hermes.observer.v1",
        }
        self.collector.on_pre_approval_request(**common)
        self.collector.on_post_approval_response(**common, choice="once")
        self.collector.on_pre_approval_request(**common)
        self.collector.on_post_approval_response(**common, choice="deny")

        events = store.session_approvals(self.conn, "s1")
        paired = [event for event in events if event["capture_state"] == "paired"]
        request_only = [event for event in events if event["capture_state"] == "request_only"]
        response_only = [event for event in events if event["capture_state"] == "response_only"]
        self.assertEqual(len(paired), 1)
        self.assertEqual(paired[0]["outcome"], "allowed")
        self.assertEqual(len(request_only), 1)
        self.assertEqual(request_only[0]["outcome"], "unknown")
        self.assertEqual(len(response_only), 1)
        self.assertEqual(response_only[0]["outcome"], "denied")

    def test_persisted_request_key_stays_quarantined_after_collector_restart(self):
        common = {
            "session_key": "s1", "turn_id": "restart-turn", "tool_call_id": "restart-tool-call",
            "surface": "gateway", "pattern_key": "sudo",
            "telemetry_schema_version": "hermes.observer.v1",
        }
        self.collector.on_pre_approval_request(**common)

        restarted = collector.Collector(lambda: nullcontext(self.conn))
        restarted.on_session_start(
            session_id="s1", telemetry_schema_version="hermes.observer.v1"
        )
        restarted.on_pre_approval_request(**common)
        restarted.on_post_approval_response(**common, choice="deny")

        events = store.session_approvals(self.conn, "s1")
        self.assertEqual(sum(event["capture_state"] == "paired" for event in events), 0)
        self.assertEqual(sum(event["capture_state"] == "request_only" for event in events), 2)
        self.assertEqual(sum(event["capture_state"] == "response_only" for event in events), 1)

    def test_persisted_paired_key_stays_quarantined_after_collector_restart(self):
        common = {
            "session_key": "s1", "turn_id": "paired-restart-turn",
            "tool_call_id": "paired-restart-tool-call", "surface": "gateway",
            "pattern_key": "sudo", "telemetry_schema_version": "hermes.observer.v1",
        }
        self.collector.on_pre_approval_request(**common)
        self.collector.on_post_approval_response(**common, choice="once")

        restarted = collector.Collector(lambda: nullcontext(self.conn))
        restarted.on_session_start(
            session_id="s1", telemetry_schema_version="hermes.observer.v1"
        )
        restarted.on_pre_approval_request(**common)
        restarted.on_post_approval_response(**common, choice="deny")

        events = store.session_approvals(self.conn, "s1")
        paired = [event for event in events if event["capture_state"] == "paired"]
        request_only = [event for event in events if event["capture_state"] == "request_only"]
        response_only = [event for event in events if event["capture_state"] == "response_only"]
        self.assertEqual(len(paired), 1)
        self.assertEqual(paired[0]["outcome"], "allowed")
        self.assertEqual(len(request_only), 1)
        self.assertEqual(len(response_only), 1)
        self.assertEqual(response_only[0]["outcome"], "denied")

    def test_approval_observer_failures_do_not_escape_collector_callbacks(self):
        def unavailable_connection():
            raise OSError("state database unavailable")

        collector_with_failure = collector.Collector(unavailable_connection)
        with self.assertLogs("observability_core.collector", level="WARNING"):
            collector_with_failure.on_session_start(
                session_id="s1", telemetry_schema_version="hermes.observer.v1"
            )
            collector_with_failure.on_pre_approval_request(
                session_key="s1", turn_id="turn", choice="once",
                telemetry_schema_version="hermes.observer.v1",
            )
            collector_with_failure.on_post_approval_response(
                session_key="s1", turn_id="turn", choice="once",
                telemetry_schema_version="hermes.observer.v1",
            )


if __name__ == "__main__":
    unittest.main()
