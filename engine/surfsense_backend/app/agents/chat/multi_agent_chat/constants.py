"""Connector-type to subagent name; subagent name to availability tokens for build_subagents."""

from __future__ import annotations

# Third-party connectors, hosted MCP and the Dropbox/Drive/OneDrive subagents were
# removed from the Monoshiri fork, so no connector type routes to a subagent.
CONNECTOR_TYPE_TO_CONNECTOR_AGENT_MAPS: dict[str, str] = {}

# Every remaining subagent is always available (empty token set).
SUBAGENT_TO_REQUIRED_CONNECTOR_MAP: dict[str, frozenset[str]] = {
    "deliverables": frozenset(),
    "knowledge_base": frozenset(),
    "web_crawler": frozenset(),
    "youtube": frozenset(),
    "google_search": frozenset(),
}

# Old per-connector subagent names used to resolve to ``mcp_discovery`` for
# checkpoint resume. That subagent is gone, so there is nothing to alias to.
LEGACY_SUBAGENT_ALIASES: dict[str, str] = {}
