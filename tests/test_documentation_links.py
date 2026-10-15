import unittest

from observability_core.documentation_links import (
    configured_documentation_links,
    sanitize_documentation_links,
)
from observability_core.service import configured_documentation_links as plugin_documentation_links


class DocumentationLinkTests(unittest.TestCase):
    def test_profile_settings_are_scoped_to_the_exact_plugin(self):
        configured = {"mcp__server__search": "https://docs.example.invalid/search"}
        config = {
            "plugins": {"entries": {
                "hermes-observability-plugin": {"settings": {"documentation_links": configured}},
                "other-plugin": {"settings": {"documentation_links": {"terminal": "https://docs.example.invalid/other"}}},
            }}
        }

        self.assertEqual(configured_documentation_links(config, "hermes-observability-plugin"), configured)
        self.assertEqual(plugin_documentation_links(config), configured)
        self.assertEqual(configured_documentation_links(config, "missing-plugin"), {})

    def test_legacy_plugin_config_is_used_only_when_no_settings_value_exists(self):
        links = {"tool": "https://docs.example.invalid/tool"}
        legacy = {"plugins": {"entries": {"observability": {"config": {"documentation_links": links}}}}}
        override = {"plugins": {"entries": {"observability": {
            "settings": {"documentation_links": {}}, "config": {"documentation_links": links}
        }}}}

        self.assertEqual(configured_documentation_links(legacy, "observability"), links)
        self.assertEqual(configured_documentation_links(override, "observability"), {})

    def test_only_explicit_exact_https_links_are_accepted(self):
        supplied = {
            "mcp__server__search": "https://docs.example.invalid/search#required-query",
            "terminal": "http://docs.example.invalid/terminal",
            "mcp__server": "https://docs.example.invalid/near-match",
            "unsafe": "javascript:alert(1)",
            "userinfo": "https://alice:secret@docs.example.invalid/private",
            " whitespace ": "https://docs.example.invalid/whitespace",
            "bad-url": "https://",
        }

        result = sanitize_documentation_links(supplied)

        self.assertEqual(result, {
            "mcp__server__search": "https://docs.example.invalid/search#required-query",
            "mcp__server": "https://docs.example.invalid/near-match",
        })

    def test_invalid_container_or_non_string_entries_produce_no_links(self):
        self.assertEqual(sanitize_documentation_links(None), {})
        self.assertEqual(sanitize_documentation_links(["https://docs.example.invalid"]), {})
        self.assertEqual(sanitize_documentation_links({"tool": None, 1: "https://docs.example.invalid"}), {})


if __name__ == "__main__":
    unittest.main()
