"""Validation and exact-identity binding for opt-in tool documentation links."""
from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit
from typing import Any


def configured_documentation_links(config: Any, plugin_id: str) -> dict[str, str]:
    """Read only this plugin's settings map, with the legacy config subtree fallback."""
    if not isinstance(config, Mapping):
        return {}
    plugins = config.get("plugins")
    entries = plugins.get("entries") if isinstance(plugins, Mapping) else None
    entry = entries.get(plugin_id) if isinstance(entries, Mapping) else None
    if not isinstance(entry, Mapping):
        return {}
    settings = entry.get("settings")
    links = settings.get("documentation_links") if isinstance(settings, Mapping) else None
    if links is None:
        legacy = entry.get("config")
        links = legacy.get("documentation_links") if isinstance(legacy, Mapping) else None
    return dict(links) if isinstance(links, Mapping) else {}


def sanitize_documentation_links(value: Any) -> dict[str, str]:
    """Accept only explicit exact-tool HTTPS URLs; never normalize or guess identities."""
    if not isinstance(value, Mapping):
        return {}

    accepted: dict[str, str] = {}
    for identity, url in value.items():
        if (not isinstance(identity, str) or not identity or identity != identity.strip()
                or len(identity) > 256):
            continue
        if (not isinstance(url, str) or not url or url != url.strip() or len(url) > 2048
                or any(character.isspace() for character in url)):
            continue
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
        except ValueError:
            continue
        if parsed.scheme != "https" or not parsed.netloc or not hostname:
            continue
        if parsed.username is not None or parsed.password is not None:
            continue
        try:
            parsed.port
        except ValueError:
            continue
        accepted[identity] = url
    return accepted


def attach_documentation_links(overview: dict[str, Any], configured: Any) -> dict[str, Any]:
    """Bind configured links to tool breakdown and findings by exact observer identity."""
    links = sanitize_documentation_links(configured)
    if not links:
        return overview
    for family in ("tool_breakdown", "repeated_failures"):
        for record in overview.get(family, []):
            url = links.get(record.get("tool_name"))
            if url is not None:
                record["documentation_url"] = url
    return overview
