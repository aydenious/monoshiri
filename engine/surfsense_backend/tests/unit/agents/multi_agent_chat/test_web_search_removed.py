"""Guardrail: the multi-engine ``web_search`` tool is fully retired.

Public web search now runs exclusively through the ``google_search`` subagent.
This pins that invariant so a future change can't quietly bring the
``web_search`` tool back.
"""

from __future__ import annotations

import pytest

from app.agents.chat.multi_agent_chat.main_agent.tools.index import (
    MAIN_AGENT_SURFSENSE_TOOL_NAMES,
)
from app.agents.chat.multi_agent_chat.subagents.registry import (
    SUBAGENT_BUILDERS_BY_NAME,
)

pytestmark = pytest.mark.unit


def test_web_search_tool_removed_from_main_agent():
    assert "web_search" not in MAIN_AGENT_SURFSENSE_TOOL_NAMES


def test_google_search_specialist_present():
    assert "google_search" in SUBAGENT_BUILDERS_BY_NAME
