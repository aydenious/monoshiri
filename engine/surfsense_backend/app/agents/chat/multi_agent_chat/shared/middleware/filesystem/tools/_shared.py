"""Pieces shared by the filesystem tool factories (tool assembly, rm/rmdir dispatch)."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING, Annotated, Any

from deepagents.backends.utils import validate_path
from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.backends.git_tree import (
    GitTreeBackend,
)
from app.agents.chat.multi_agent_chat.shared.state.filesystem_state import (
    SurfSenseFilesystemState,
)

from ..middleware.async_dispatch import run_async_blocking
from ..middleware.path_resolution import resolve_relative

if TYPE_CHECKING:
    from ..middleware import SurfSenseFilesystemMiddleware

Runtime = ToolRuntime[None, SurfSenseFilesystemState]
DeleteBranch = Callable[
    ["SurfSenseFilesystemMiddleware", Runtime, str], Awaitable[Command | str]
]


def stages_in_cloud(backend: Any) -> bool:
    """True when writes are staged for end of turn (the git-tree backend writes directly)."""
    return not isinstance(backend, GitTreeBackend)


def build_tool(
    *,
    name: str,
    description: str,
    coroutine: Callable[..., Any],
    metadata: dict[str, Any],
) -> BaseTool:
    """Wrap ``coroutine`` as a tool; the sync entry point blocks on the same coroutine."""

    @wraps(coroutine)
    def sync(*args: Any, **kwargs: Any) -> Any:
        return run_async_blocking(coroutine(*args, **kwargs))

    return StructuredTool.from_function(
        name=name,
        description=description,
        func=sync,
        coroutine=coroutine,
        metadata=metadata,
    )


def build_delete_tool(
    mw: "SurfSenseFilesystemMiddleware",
    *,
    name: str,
    description: str,
    path_doc: str,
    cloud: DeleteBranch,
    direct: DeleteBranch,
    metadata: dict[str, Any],
) -> BaseTool:
    """``rm`` / ``rmdir`` tool: resolve + validate, then stage or delete directly."""

    async def delete(
        path: Annotated[str, path_doc],
        runtime: Runtime,
    ) -> Command | str:
        if not path or not path.strip():
            return "Error: path is required."

        target = resolve_relative(path, runtime)
        try:
            validated = validate_path(target)
        except ValueError as exc:
            return f"Error: {exc}"

        backend = mw._get_backend(runtime)
        if stages_in_cloud(backend):
            return await cloud(mw, runtime, validated)
        return await direct(mw, runtime, validated)

    return build_tool(
        name=name, description=description, coroutine=delete, metadata=metadata
    )
