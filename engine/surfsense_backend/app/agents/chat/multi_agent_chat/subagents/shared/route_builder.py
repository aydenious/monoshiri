"""Shared ``build_subagent`` body for routes with a ``tools/index.py`` and two ``.md`` files."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agents.chat.multi_agent_chat.subagents.shared.md_file_reader import (
    read_md_file,
)
from app.agents.chat.multi_agent_chat.subagents.shared.spec import SurfSenseSubagentSpec
from app.agents.chat.multi_agent_chat.subagents.shared.subagent_builder import (
    pack_subagent,
)


def build_route_subagent(
    package: str,
    tools_index: ModuleType,
    fallback_description: str,
    *,
    dependencies: dict[str, Any],
    model: BaseChatModel | None = None,
    middleware_stack: dict[str, Any] | None = None,
    mcp_tools: list[BaseTool] | None = None,
    system_prompt_suffix: str = "",
) -> SurfSenseSubagentSpec:
    """Build a route's spec from its ``description.md`` / ``system_prompt.md`` and tools.

    ``tools_index`` supplies ``NAME``, ``RULESET`` and ``load_tools``.
    """
    tools = [*tools_index.load_tools(dependencies=dependencies), *(mcp_tools or [])]
    description = read_md_file(package, "description").strip() or fallback_description
    system_prompt = read_md_file(package, "system_prompt").strip()
    if system_prompt_suffix:
        system_prompt = f"{system_prompt}\n\n{system_prompt_suffix}"
    return pack_subagent(
        name=tools_index.NAME,
        description=description,
        system_prompt=system_prompt,
        tools=tools,
        ruleset=tools_index.RULESET,
        dependencies=dependencies,
        model=model,
        middleware_stack=middleware_stack,
    )
