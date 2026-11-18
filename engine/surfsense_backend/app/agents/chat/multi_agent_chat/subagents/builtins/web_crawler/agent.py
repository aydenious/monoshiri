"""``web_crawler`` route: ``SurfSenseSubagentSpec`` builder for deepagents."""

from __future__ import annotations

from typing import Any

from app.agents.chat.multi_agent_chat.subagents.shared.route_builder import (
    build_route_subagent,
)
from app.agents.chat.multi_agent_chat.subagents.shared.spec import SurfSenseSubagentSpec

from .tools import index


def build_subagent(**kwargs: Any) -> SurfSenseSubagentSpec:
    return build_route_subagent(
        __package__,
        index,
        "Crawls live public web pages and whole sites for this workspace.",
        **kwargs,
    )
