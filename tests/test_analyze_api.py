"""Focused tests for the policy-install API without requiring FastAPI at import time."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from observability_core import analyze_guard, runtime, service


class _Router:
    def get(self, *_args, **_kwargs):
        return lambda function: function

    def post(self, *_args, **_kwargs):
        return lambda function: function


class _HTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _load_api_module():
    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.APIRouter = _Router
    fastapi_stub.HTTPException = _HTTPException
    fastapi_stub.Query = lambda default=None, **_kwargs: default
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("analyze_api_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"fastapi": fastapi_stub}):
        spec.loader.exec_module(module)
    return module


api = _load_api_module()


class AnalyzePolicyApiTests(unittest.TestCase):
    def test_runtime_profile_name_uses_the_active_hermes_home(self):
        hermes_constants_stub = types.ModuleType("hermes_constants")
        hermes_constants_stub.get_hermes_home = lambda: Path("/profiles/personal")
        hermes_constants_stub.profile_name_for_home = lambda home: (
            "personal" if Path(home) == Path("/profiles/personal") else None
        )

        with patch.dict(sys.modules, {"hermes_constants": hermes_constants_stub}):
            self.assertEqual(runtime.profile_name(), "personal")

    def test_documentation_links_are_read_from_this_plugin_profile_settings(self):
        links = {"mcp__server__search": "https://docs.example.invalid/search"}
        config_module = types.ModuleType("hermes_cli.config")
        setattr(config_module, "load_config", lambda: {"plugins": {"entries": {
            "hermes-observability-plugin": {"settings": {"documentation_links": links}},
            "other-plugin": {"settings": {"documentation_links": {"terminal": "https://docs.example.invalid/other"}}},
        }}})
        hermes_cli_module = types.ModuleType("hermes_cli")
        setattr(hermes_cli_module, "__path__", [])

        with patch.dict(sys.modules, {
            "hermes_cli": hermes_cli_module,
            "hermes_cli.config": config_module,
        }):
            result = api._configured_documentation_links(service)

        self.assertEqual(result, links)

    def test_endpoint_installs_the_real_profile_local_read_only_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)

            class Runtime:
                @staticmethod
                def profile_home():
                    return home

                @staticmethod
                def profile_name():
                    return "personal"

            with patch.object(api, "_core", return_value=(None, None, Runtime())):
                with patch.object(api.importlib, "import_module", return_value=analyze_guard):
                    result = api.lock_analyze_session("runtime-safe-1", expected_profile="personal")

            self.assertEqual(result, {"read_only": True, "profile": "personal"})
            self.assertTrue(analyze_guard.is_read_only_session("runtime-safe-1", home))

    def test_endpoint_rejects_invalid_session_identifier(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            class Runtime:
                @staticmethod
                def profile_home():
                    return Path(temp_dir)

                @staticmethod
                def profile_name():
                    return "personal"

            with patch.object(api, "_core", return_value=(None, None, Runtime())):
                with patch.object(api.importlib, "import_module", return_value=analyze_guard):
                    with self.assertRaises(_HTTPException) as raised:
                        api.lock_analyze_session("", expected_profile="personal")

            self.assertEqual(raised.exception.status_code, 422)

    def test_endpoint_rejects_profile_mismatch_before_installing_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)

            class Runtime:
                @staticmethod
                def profile_home():
                    return home

                @staticmethod
                def profile_name():
                    return "personal"

            with patch.object(api, "_core", return_value=(None, None, Runtime())):
                with self.assertRaises(_HTTPException) as raised:
                    api.lock_analyze_session("runtime-safe-2", expected_profile="work")

            self.assertEqual(raised.exception.status_code, 409)
            self.assertFalse(analyze_guard.is_read_only_session("runtime-safe-2", home))

    def test_call_detail_returns_and_validates_the_connected_profile(self):
        from contextlib import contextmanager

        class Runtime:
            @staticmethod
            def profile_name():
                return "personal"

            @staticmethod
            @contextmanager
            def profile_connection():
                yield object()

        class Service:
            @staticmethod
            def call_detail(_conn, session_id, call_id):
                return {"session_id": session_id, "call_id": call_id}

        result = None
        with patch.object(api, "_core", return_value=(None, Service(), Runtime())):
            result = api.call_detail("s1", "c1", expected_profile="personal")
            with self.assertRaises(_HTTPException) as raised:
                api.call_detail("s1", "c1", expected_profile="work")

        self.assertEqual(result["profile"], "personal")
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
