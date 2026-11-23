"""``deliverables`` route: ``SurfSenseSubagentSpec`` builder for deepagents.

Tools self-gate inside their bodies via :func:`request_approval`; the
empty :data:`tools.index.RULESET` is layered into a per-subagent
:class:`PermissionMiddleware` for uniformity.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.tool_error import ToolErrorMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agents.chat.multi_agent_chat.subagents.shared.route_builder import (
    build_route_subagent,
)
from app.agents.chat.multi_agent_chat.subagents.shared.spec import SurfSenseSubagentSpec
from app.config import config
from app.sandbox import is_sandbox_enabled

from .middleware import ArtifactRosterMiddleware
from .tools import index

_INTERACTIVE_VIDEO_PROMPT = """
**Video requests.** Call `enqueue_deliverable_job` exactly once and return its
Receipt immediately.
""".strip()


def _tool_error_content(exc: Exception, request: Any) -> str | None:
    """Return a tool's ``ValueError`` to the model so it can fix its arguments.

    Tool messages are written for the model. Anything else (for example
    ``SandboxUnavailableError``, whose diagnostic iOS reads) must end the turn.
    """
    if isinstance(exc, ValueError):
        return f"Error: {exc}"
    return None


def build_subagent(
    *,
    dependencies: dict[str, Any],
    model: BaseChatModel | None = None,
    middleware_stack: dict[str, Any] | None = None,
    mcp_tools: list[BaseTool] | None = None,
) -> SurfSenseSubagentSpec:
    suffix = (
        _INTERACTIVE_VIDEO_PROMPT
        if config.VIDEO_SANDBOX_RENDERING_ENABLED and is_sandbox_enabled()
        else ""
    )
    return build_route_subagent(
        __package__,
        index,
        "Handles deliverables tasks for this workspace.",
        dependencies=dependencies,
        model=model,
        middleware_stack={
            **(middleware_stack or {}),
            "artifact_roster": ArtifactRosterMiddleware(
                workspace_id=dependencies["workspace_id"]
            ),
            "tool_errors": ToolErrorMiddleware(_tool_error_content),
        },
        mcp_tools=mcp_tools,
        system_prompt_suffix=suffix,
    )
