import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

from observability_core import collector, service, store


class ProviderAttemptTests(unittest.TestCase):
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

    def test_provider_attempts_are_correlated_and_separate_from_tool_metrics(self):
        common = {
            "session_id": "s1", "task_id": "task-1", "turn_id": "turn-1",
            "provider": "provider-x", "model": "model-y", "telemetry_schema_version": "hermes.observer.v1",
        }
        self.collector.on_pre_api_request(
            **common, api_request_id=" opaque/request-A ", api_call_count=1,
            started_at=1000.0, request={"messages": [{"content": "private"}]},
            future_field={"secret": "must not persist"},
        )
        self.collector.on_post_api_request(
            **common, api_request_id=" opaque/request-A ", api_call_count=1,
            started_at=1000.0, ended_at=1000.25, api_duration=0.25,
            usage={"input_tokens": 12, "output_tokens": 4, "total_tokens": 16,
                   "provider_private": "do not persist"},
            response={"reasoning": "never store", "content": "private"},
            response_model="model-y-v2", finish_reason="stop",
        )
        self.collector.on_pre_api_request(
            **common, api_request_id="opaque/request-B", api_call_count=2,
            started_at=1001.0, request={"messages": [{"content": "private"}]},
        )
        self.collector.on_api_request_error(
            **common, api_request_id="opaque/request-B", api_call_count=2,
            started_at=1001.0, ended_at=1002.0, api_duration=1.0,
            status_code=504, retry_count=1, max_retries=3, retryable=True,
            reason="timeout", error={"type": "TimeoutError", "message": "private"},
            request={"secret": "must not persist"},
        )
        self.collector.on_pre_tool_call(session_id="s1", tool_name="files.read", tool_call_id="tool-1")
        self.collector.on_post_tool_call(
            session_id="s1", tool_name="files.read", tool_call_id="tool-1", status="ok"
        )

        attempts = store.session_provider_attempts(self.conn, "s1")
        self.assertEqual([row["api_request_id"] for row in attempts], [" opaque/request-A ", "opaque/request-B"])
        self.assertEqual([row["outcome"] for row in attempts], ["ok", "failed"])
        self.assertEqual(attempts[0]["usage"], {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16})
        self.assertEqual(attempts[1]["error_type"], "TimeoutError")
        self.assertEqual(attempts[1]["status_code"], 504)
        self.assertEqual(attempts[1]["retry_count"], 1)
        self.assertEqual(attempts[1]["retryable"], 1)
        self.assertNotIn("private", repr(attempts))
        self.assertNotIn("secret", repr(attempts))
        self.assertNotIn("reasoning", repr(attempts))
        self.assertEqual(store.metrics(self.conn)["calls"]["total"], 1)
        self.assertEqual(store.metrics(self.conn)["calls"]["ok"], 1)

        provider = store.provider_metrics(self.conn)
        self.assertEqual(provider["attempts"]["total"], 2)
        self.assertEqual(provider["attempts"]["ok"], 1)
        self.assertEqual(provider["attempts"]["failed"], 1)
        self.assertEqual(provider["retryable_failures"], 1)
        self.assertEqual(provider["observed_sessions"], 2)
        self.assertEqual(provider["unknown_sessions"], 0)
        self.assertEqual(
            store.provider_session_coverage(self.conn, "s1")["coverage_state"],
            "observed_no_known_gaps",
        )
        raw_rows = [tuple(row) for row in self.conn.execute("SELECT * FROM hermes_observability_provider_attempts")]
        self.assertNotIn("private", repr(raw_rows))
        self.assertNotIn("reasoning", repr(raw_rows))
        self.assertNotIn("secret", repr(raw_rows))

    def test_incomplete_lifecycles_and_unknown_schema_are_not_reported_as_zero(self):
        self.collector.on_pre_api_request(
            session_id="s1", turn_id="t1", api_request_id="pending", provider="p", model="m",
            telemetry_schema_version="hermes.observer.v1", started_at=3000.0,
        )
        self.collector.on_post_api_request(
            session_id="s1", turn_id="t2", api_request_id="post-only", provider="p", model="m",
            telemetry_schema_version="hermes.observer.v1", usage={"output_tokens": 2},
        )
        self.collector.on_pre_api_request(
            session_id="s2", turn_id="t3", api_request_id="unknown-schema", provider="p", model="m",
            telemetry_schema_version="future.observer.v99", started_at=3000.0,
        )
        attempts = store.session_provider_attempts(self.conn, "s1")
        self.assertEqual(len(attempts), 2)
        self.assertEqual({row["capture_state"] for row in attempts}, {"start_only", "post_only"})
        provider = store.provider_metrics(self.conn)
        self.assertEqual(provider["partial_sessions"], 1)
        self.assertEqual(provider["unknown_sessions"], 1)
        self.assertEqual(provider["observed_sessions"], 0)
        self.assertIsNone(provider["success_rate"])
        self.assertEqual(store.provider_session_coverage(self.conn, "s1")["coverage_state"], "partial")
        self.assertEqual(store.provider_session_coverage(self.conn, "s2")["coverage_state"], "unknown")

    def test_retry_fields_remain_attached_to_the_exact_api_attempt(self):
        base = {
            "session_id": "s1", "turn_id": "same-turn", "provider": "p", "model": "m",
            "telemetry_schema_version": "hermes.observer.v1",
        }
        for request_id, call_count, retry_count in (("attempt/1", 1, 0), ("attempt/2", 2, 1)):
            self.collector.on_pre_api_request(
                **base, api_request_id=request_id, api_call_count=call_count, started_at=call_count * 10.0,
            )
            self.collector.on_api_request_error(
                **base, api_request_id=request_id, api_call_count=call_count,
                retry_count=retry_count, status_code=503, retryable=True,
                error={"type": "ServiceUnavailable"},
            )

        attempts = store.session_provider_attempts(self.conn, "s1")
        self.assertEqual(
            [(row["api_request_id"], row["api_call_count"], row["retry_count"]) for row in attempts],
            [("attempt/1", 1, 0), ("attempt/2", 2, 1)],
        )

    def test_unpaired_completion_is_visible_but_not_double_counted_as_attempt(self):
        self.collector.on_pre_api_request(
            session_id="s1", task_id="task", turn_id="turn-a", api_request_id="same-id",
            provider="p", model="m", telemetry_schema_version="hermes.observer.v1",
        )
        self.collector.on_post_api_request(
            session_id="s1", task_id="task", turn_id="turn-b", api_request_id="same-id",
            provider="p", model="m", telemetry_schema_version="hermes.observer.v1",
        )

        metrics = store.provider_metrics(self.conn)
        rows = store.session_provider_attempts(self.conn, "s1")
        self.assertEqual(metrics["attempts"]["total"], 1)
        self.assertEqual(metrics["unpaired_events"], 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["capture_state"] for row in rows}, {"start_only", "post_only"})
        self.assertEqual(store.provider_session_coverage(self.conn, "s1")["coverage_state"], "partial")

    def test_api_completion_without_any_start_is_not_counted_as_a_confirmed_attempt(self):
        self.collector.on_api_request_error(
            session_id="s1", turn_id="turn", api_request_id="", provider="p", model="m",
            status_code=504, retryable=True, telemetry_schema_version="hermes.observer.v1",
        )

        metrics = store.provider_metrics(self.conn)
        self.assertEqual(metrics["attempts"]["total"], 0)
        self.assertEqual(metrics["attempts"]["failed"], 0)
        self.assertEqual(metrics["unpaired_events"], 1)
        self.assertEqual(metrics["retryable_failures"], 0)

    def test_legacy_captured_session_without_family_marker_is_unknown_not_uncaptured(self):
        self.conn.execute(
            "INSERT INTO sessions (id, started_at, title) VALUES ('legacy', 300.0, 'Legacy')"
        )
        self.conn.commit()
        store.register_session(self.conn, "legacy", observer_schema_version="hermes.observer.v1")

        metrics = store.provider_metrics(self.conn)

        self.assertEqual(metrics["unknown_sessions"], 1)
        self.assertEqual(metrics["not_captured_sessions"], 0)
        self.assertEqual(store.provider_session_coverage(self.conn, "legacy")["coverage_state"], "unknown")

    def test_service_exposes_provider_metrics_and_session_attempt_trace(self):
        event = {
            "session_id": "s1", "turn_id": "turn-1", "api_request_id": "request-1",
            "provider": "p", "model": "m", "telemetry_schema_version": "hermes.observer.v1",
        }
        self.collector.on_pre_api_request(**event, api_call_count=1, started_at=1000.0)
        self.collector.on_post_api_request(**event, api_call_count=1, usage={"output_tokens": 3})

        overview = service.overview(self.conn)
        trace = service.session_trace(self.conn, "s1")

        self.assertEqual(overview["provider_attempts"]["attempts"]["total"], 1)
        self.assertEqual(trace["provider_attempts"][0]["api_request_id"], "request-1")
        self.assertEqual(trace["provider_attempts"][0]["usage"], {"output_tokens": 3})

    def test_register_subscribes_to_documented_provider_api_lifecycle_hooks(self):
        class Context:
            def __init__(self):
                self.hooks = {}

            def register_hook(self, name, callback):
                self.hooks[name] = callback

        context = Context()
        collector.register(context, lambda: nullcontext(self.conn))
        self.assertTrue({"pre_api_request", "post_api_request", "api_request_error"}.issubset(context.hooks))


if __name__ == "__main__":
    unittest.main()
