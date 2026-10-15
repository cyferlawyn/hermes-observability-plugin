import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

from observability_core import collector, service, store


class SubagentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "state.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, title TEXT)"
        )
        self.conn.executemany(
            "INSERT INTO sessions (id, started_at, title) VALUES (?, ?, ?)",
            [("parent", 100.0, "Parent"), ("child-a", 110.0, "Child A"), ("child-b", 120.0, "Child B")],
        )
        self.conn.commit()
        store.initialize(self.conn)
        self.collector = collector.Collector(lambda: nullcontext(self.conn))
        self.collector.on_session_start(
            session_id="parent", telemetry_schema_version="hermes.observer.v1"
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_start_stop_records_parent_child_summary_and_independent_coverage(self):
        self.collector.on_subagent_start(
            parent_session_id="parent", parent_turn_id="turn-1", child_session_id="child-a",
            child_subagent_id="worker-a", child_role="researcher", child_goal="Find the cause",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", parent_turn_id="turn-1", child_session_id="child-a",
            child_subagent_id="worker-a", child_role="researcher", child_status="completed",
            child_summary="Found the cause", duration_ms=1250,
            tool_call_history=[{
                "tool_name": "run_command", "arguments": "TOKEN=private-value", "result": "private output",
            }],
            telemetry_schema_version="hermes.observer.v1",
        )

        trace = service.session_trace(self.conn, "parent")
        assert trace is not None
        [event] = trace["subagents"]
        self.assertEqual(event["parent_session_id"], "parent")
        self.assertEqual(event["child_session_id"], "child-a")
        self.assertEqual(event["child_goal"], "Find the cause")
        self.assertEqual(event["status"], "succeeded")
        self.assertEqual(event["duration_ms"], 1250)
        self.assertEqual(event["child_summary"], "Found the cause")
        self.assertEqual(event["capture_state"], "paired")
        self.assertNotIn("tool_call_history", event)
        self.assertNotIn("private-value", repr(trace))
        self.assertNotIn("private output", repr(trace))
        self.assertEqual(trace["subagent_coverage"]["coverage_state"], "observed_no_known_gaps")
        self.assertEqual(store.metrics(self.conn)["calls"]["total"], 0)
        self.assertEqual(store.subagent_metrics(self.conn)["events"]["succeeded"], 1)

    def test_multiple_children_are_paired_by_identity_not_arrival_order(self):
        for child, worker in (("child-a", "worker-a"), ("child-b", "worker-b")):
            self.collector.on_subagent_start(
                parent_session_id="parent", parent_turn_id="turn-2", child_session_id=child,
                child_subagent_id=worker, child_goal=f"Goal for {worker}",
                telemetry_schema_version="hermes.observer.v1",
            )
        self.collector.on_subagent_stop(
            parent_session_id="parent", parent_turn_id="turn-2", child_session_id="child-b",
            child_subagent_id="worker-b", child_status="error", child_summary="B failed",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", parent_turn_id="turn-2", child_session_id="child-a",
            child_subagent_id="worker-a", child_status="success", child_summary="A done",
            telemetry_schema_version="hermes.observer.v1",
        )

        events = store.session_subagents(self.conn, "parent")
        by_child = {event["child_session_id"]: event for event in events}
        self.assertEqual(by_child["child-a"]["status"], "succeeded")
        self.assertEqual(by_child["child-a"]["child_summary"], "A done")
        self.assertEqual(by_child["child-b"]["status"], "failed")
        self.assertEqual(by_child["child-b"]["child_summary"], "B failed")
        self.assertEqual(store.subagent_session_coverage(self.conn, "parent")["coverage_state"], "observed_no_known_gaps")

    def test_missing_ids_and_interrupted_or_unmatched_lifecycle_are_explicit_partial(self):
        self.collector.on_subagent_start(
            parent_session_id="parent", child_goal="No stable child identity",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_status="interrupted", duration_ms=30,
            child_summary="Stopped early", telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-a", child_status="failed",
            child_summary="No observed start", telemetry_schema_version="hermes.observer.v1",
        )

        events = store.session_subagents(self.conn, "parent")
        self.assertEqual(len(events), 3)
        self.assertEqual({event["capture_state"] for event in events}, {"start_only", "stop_only"})
        self.assertIn("interrupted", {event["status"] for event in events})
        self.assertEqual(store.subagent_session_coverage(self.conn, "parent")["coverage_state"], "partial")
        self.assertEqual(store.subagent_metrics(self.conn)["unmatched_stops"], 2)

    def test_child_navigation_and_deletion_cascade_remove_relationship_event(self):
        self.collector.on_subagent_start(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            child_status="success", telemetry_schema_version="hermes.observer.v1",
        )
        child_trace = service.session_trace(self.conn, "child-a")
        assert child_trace is not None
        self.assertEqual(child_trace["subagents"][0]["relationship"], "child")
        self.assertEqual(child_trace["subagents"][0]["parent_session_id"], "parent")

        self.conn.execute("DELETE FROM sessions WHERE id = 'child-a'")
        self.conn.commit()
        self.assertEqual(store.session_subagents(self.conn, "parent"), [])
        self.assertEqual(store.subagent_metrics(self.conn)["events"]["total"], 0)

        self.collector.on_subagent_start(
            parent_session_id="parent", child_session_id="child-b", child_subagent_id="worker-b",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-b", child_subagent_id="worker-b",
            child_status="success", telemetry_schema_version="hermes.observer.v1",
        )
        self.conn.execute("DELETE FROM sessions WHERE id = 'parent'")
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM hermes_observability_subagents").fetchone()[0], 0
        )

    def test_related_session_trace_can_cross_the_since_reset_window(self):
        self.collector.on_subagent_start(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            child_status="success", telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_session_start(
            session_id="child-a", telemetry_schema_version="hermes.observer.v1"
        )
        store.reset_metrics(self.conn, reset_at=105.0)

        self.assertIsNone(service.session_trace(self.conn, "parent", window="Since Reset"))
        child_trace = service.session_trace(self.conn, "child-a", window="Since Reset")
        assert child_trace is not None
        [relationship] = child_trace["subagents"]
        self.assertEqual(relationship["relationship"], "child")
        self.assertTrue(relationship["parent_session_available"])
        self.assertIsNotNone(service.session_trace(self.conn, "parent", window="Overall"))
        plugin_js = (Path(__file__).parents[1] / "desktop" / "plugin.js").read_text()
        self.assertIn("/trace?window=Overall&timezone=", plugin_js)

    def test_delayed_child_source_row_is_linked_for_delete_cascade(self):
        self.conn.execute("DELETE FROM sessions WHERE id = 'child-b'")
        self.conn.commit()
        self.collector.on_subagent_start(
            parent_session_id="parent", child_session_id="child-b", child_subagent_id="worker-b",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-b", child_subagent_id="worker-b",
            child_status="success", telemetry_schema_version="hermes.observer.v1",
        )
        [row] = self.conn.execute(
            "SELECT child_session_id FROM hermes_observability_subagents"
        ).fetchall()
        self.assertIsNone(row["child_session_id"])
        [event] = store.session_subagents(self.conn, "parent")
        self.assertEqual(event["child_session_id"], "child-b")
        self.assertFalse(event["child_session_available"])

        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES ('child-b', 121.0, 'Late child')"
        )
        self.conn.commit()
        self.collector.on_session_start(
            session_id="child-b", telemetry_schema_version="hermes.observer.v1"
        )
        [row] = self.conn.execute(
            "SELECT child_session_id FROM hermes_observability_subagents"
        ).fetchall()
        self.assertEqual(row["child_session_id"], "child-b")
        [event] = store.session_subagents(self.conn, "parent")
        self.assertTrue(event["child_session_available"])

        self.conn.execute("DELETE FROM sessions WHERE id = 'child-b'")
        self.conn.commit()
        self.assertEqual(store.session_subagents(self.conn, "parent"), [])

    def test_missing_parent_id_is_preserved_as_partial_even_when_child_ids_match(self):
        self.collector.on_subagent_start(
            session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            child_status="success", telemetry_schema_version="hermes.observer.v1",
        )

        [event] = store.session_subagents(self.conn, "parent")
        self.assertIsNone(event["parent_session_id"])
        self.assertEqual(event["capture_state"], "paired")
        coverage = store.subagent_session_coverage(self.conn, "parent")
        self.assertEqual(coverage["coverage_state"], "partial")
        self.assertEqual(coverage["identity_gaps"], 1)

    def test_blank_child_ids_are_not_used_as_correlation_keys(self):
        self.collector.on_subagent_start(
            parent_session_id="parent", child_session_id="   ", child_subagent_id=" ",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="   ", child_subagent_id=" ",
            child_status="success", telemetry_schema_version="hermes.observer.v1",
        )

        events = store.session_subagents(self.conn, "parent")
        self.assertEqual(len(events), 2)
        self.assertEqual({event["capture_state"] for event in events}, {"start_only", "stop_only"})
        self.assertTrue(all(event["child_session_id"] is None for event in events))
        self.assertEqual(store.subagent_session_coverage(self.conn, "parent")["coverage_state"], "partial")

    def test_pending_start_can_be_paired_after_collector_restart(self):
        self.collector.on_subagent_start(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            telemetry_schema_version="hermes.observer.v1",
        )
        restarted = collector.Collector(lambda: nullcontext(self.conn))
        restarted.on_session_start(session_id="parent", telemetry_schema_version="hermes.observer.v1")
        restarted.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            child_status="completed", telemetry_schema_version="hermes.observer.v1",
        )

        [event] = store.session_subagents(self.conn, "parent")
        self.assertEqual(event["capture_state"], "paired")
        self.assertEqual(event["status"], "succeeded")

    def test_mismatched_secondary_identity_does_not_pair_same_child_session(self):
        self.collector.on_subagent_start(
            parent_session_id="parent", parent_turn_id="turn-a", child_session_id="child-a",
            child_subagent_id="worker-a", telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", parent_turn_id="turn-b", child_session_id="child-a",
            child_subagent_id="worker-b", child_status="success",
            telemetry_schema_version="hermes.observer.v1",
        )

        events = store.session_subagents(self.conn, "parent")
        self.assertEqual({event["capture_state"] for event in events}, {"start_only", "stop_only"})
        self.assertEqual(store.subagent_session_coverage(self.conn, "parent")["coverage_state"], "partial")

    def test_duplicate_child_identity_is_never_paired_by_arrival_order(self):
        for _ in range(2):
            self.collector.on_subagent_start(
                parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
                telemetry_schema_version="hermes.observer.v1",
            )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            child_status="success", telemetry_schema_version="hermes.observer.v1",
        )

        events = store.session_subagents(self.conn, "parent")
        self.assertEqual(sum(event["capture_state"] == "paired" for event in events), 0)
        self.assertEqual(sum(event["capture_state"] == "start_only" for event in events), 2)
        self.assertEqual(sum(event["capture_state"] == "stop_only" for event in events), 1)
        self.assertEqual(store.subagent_session_coverage(self.conn, "parent")["coverage_state"], "partial")

    def test_registered_subagent_hooks_and_overview_trace_models(self):
        class Context:
            def __init__(self):
                self.hooks = {}

            def register_hook(self, name, callback):
                self.hooks[name] = callback

        context = Context()
        collector.register(context, lambda: nullcontext(self.conn))
        self.assertIn("subagent_start", context.hooks)
        self.assertIn("subagent_stop", context.hooks)
        context.hooks["on_session_start"](
            session_id="parent", telemetry_schema_version="hermes.observer.v1"
        )
        context.hooks["subagent_start"](
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="registered",
            telemetry_schema_version="hermes.observer.v1",
        )
        context.hooks["subagent_stop"](
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="registered",
            child_status="success", telemetry_schema_version="hermes.observer.v1",
        )

        overview = service.overview(self.conn)
        trace = service.session_trace(self.conn, "parent")
        self.assertEqual(overview["subagents"]["events"]["succeeded"], 1)
        assert trace is not None
        self.assertEqual(len(trace["subagents"]), 1)
        self.assertEqual(trace["subagent_coverage"]["coverage_state"], "observed_no_known_gaps")

    def test_unknown_subagent_status_is_visible_and_prevents_observed_coverage(self):
        self.collector.on_subagent_start(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-a", child_subagent_id="worker-a",
            child_status="future-terminal-state", telemetry_schema_version="hermes.observer.v1",
        )

        [event] = store.session_subagents(self.conn, "parent")
        coverage = store.subagent_session_coverage(self.conn, "parent")
        self.assertEqual(event["status"], "unknown")
        self.assertEqual(coverage["unknown_status_events"], 1)
        self.assertEqual(coverage["coverage_state"], "partial")

    def test_unsupported_schema_is_unknown_and_observer_failures_remain_fail_open(self):
        self.collector.on_session_start(
            session_id="parent", telemetry_schema_version="future.observer.v99"
        )
        self.collector.on_subagent_start(
            parent_session_id="parent", child_session_id="child-a",
            telemetry_schema_version="future.observer.v99",
        )
        self.collector.on_subagent_stop(
            parent_session_id="parent", child_session_id="child-a", child_status="success",
            telemetry_schema_version="future.observer.v99",
        )
        self.assertEqual(
            store.subagent_session_coverage(self.conn, "parent")["coverage_state"], "unknown"
        )

        def unavailable_connection():
            raise OSError("database unavailable")

        broken = collector.Collector(unavailable_connection)
        with self.assertLogs("observability_core.collector", level="WARNING"):
            broken.on_session_start(session_id="parent", telemetry_schema_version="hermes.observer.v1")
            broken.on_subagent_start(parent_session_id="parent", child_session_id="child-a")
            broken.on_subagent_stop(parent_session_id="parent", child_session_id="child-a")


if __name__ == "__main__":
    unittest.main()
