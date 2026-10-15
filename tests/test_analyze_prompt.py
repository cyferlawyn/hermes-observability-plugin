import unittest

from observability_core.analyze_prompt import build_analyze_prompt


class AnalyzePromptTests(unittest.TestCase):
    def _detail(self, *, source_status="matched", outcome="failed"):
        return {
            "available": source_status == "matched",
            "call": {
                "id": "obs-call-17",
                "session_id": "source-session-9",
                "tool_name": "files.read",
                "tool_call_id": "hermes-tool-call-42",
                "started_at": 10.0,
                "completed_at": 11.0,
                "duration_ms": 1000.0,
                "outcome": outcome,
                "error_type": "PermissionError",
                "error_code": "EACCES",
                "error_message": "permission denied",
            },
            "source": {
                "status": source_status,
                "arguments": {"path": "settings.yaml"} if source_status == "matched" else None,
                "result": "read denied" if source_status == "matched" else None,
                "result_status": "matched" if source_status == "matched" else source_status,
                "source_message_id": 42 if source_status == "matched" else None,
            },
            "context": [
                {"role": "user", "content": "Please inspect settings.yaml"},
                {"role": "assistant", "content": "I will read it."},
            ] if source_status == "matched" else [],
            # Deliberately provide fields that must never cross into the follow-up prompt.
            "reasoning": "private provider reasoning sentinel",
            "provider_payload": "raw request sentinel",
            "child_transcript": "full child transcript sentinel",
        }

    def test_failed_call_prompt_contains_only_labeled_allowlisted_evidence(self):
        prompt = build_analyze_prompt(self._detail())

        self.assertIn("Analyze this failed Hermes tool call", prompt)
        self.assertIn('Source session: "source-session-9"', prompt)
        self.assertIn('Observability call: "obs-call-17"', prompt)
        self.assertIn('Hermes tool-call ID: "hermes-tool-call-42"', prompt)
        self.assertIn('Tool: "files.read"', prompt)
        self.assertIn('Outcome: "failed"', prompt)
        self.assertIn("EACCES", prompt)
        self.assertIn('"path": "settings.yaml"', prompt)
        self.assertIn("read denied", prompt)
        self.assertIn("Please inspect settings.yaml", prompt)
        self.assertIn("technically read-only", prompt)
        for forbidden in ("private provider reasoning sentinel", "raw request sentinel", "full child transcript sentinel"):
            self.assertNotIn(forbidden, prompt)

    def test_missing_or_ambiguous_source_is_labeled_and_keeps_references(self):
        for source_status in ("missing", "ambiguous"):
            with self.subTest(source_status=source_status):
                prompt = build_analyze_prompt(self._detail(source_status=source_status))

                self.assertIn('Source session: "source-session-9"', prompt)
                self.assertIn('Observability call: "obs-call-17"', prompt)
                self.assertIn(f"Source correlation: {source_status}", prompt)
                self.assertIn("Arguments: unavailable", prompt)
                self.assertIn("Source result: unavailable", prompt)
                self.assertIn("Nearby visible conversation: unavailable", prompt)
                self.assertIn("permission denied", prompt)

    def test_non_failed_calls_cannot_be_analyzed(self):
        self.assertIsNone(build_analyze_prompt(self._detail(outcome="ok")))
        self.assertIsNone(build_analyze_prompt(self._detail(outcome="unknown")))

    def test_long_source_fields_are_bounded_and_marked(self):
        detail = self._detail()
        detail["source"]["result"] = "r" * 6000
        detail["context"] = [{"role": "assistant", "content": "c" * 2000}]

        prompt = build_analyze_prompt(detail)

        self.assertIn("[truncated]", prompt)
        self.assertLess(len(prompt), 18000)


if __name__ == "__main__":
    unittest.main()
