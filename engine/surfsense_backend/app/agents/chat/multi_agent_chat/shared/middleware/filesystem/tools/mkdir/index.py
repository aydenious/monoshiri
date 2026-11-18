"""``mkdir`` factory: stages for end-of-turn, or writes through on git-tree."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Any

from deepagents.backends.utils import validate_path
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from app.capabilities.core import ActivityDescriptor
from app.knowledge_store.paths import DOCUMENTS_ROOT

from ...middleware.path_resolution import resolve_relative
from .._shared import Runtime, build_tool, stages_in_cloud
from .description import select_description

if TYPE_CHECKING:
    from ...middleware import SurfSenseFilesystemMiddleware


def create_mkdir_tool(mw: SurfSenseFilesystemMiddleware) -> BaseTool:
    description = select_description()

    async def async_mkdir(
        path: Annotated[str, "Absolute or relative directory path to create."],
        runtime: Runtime,
    ) -> Command | str:
        target = resolve_relative(path, runtime)
        try:
            validated = validate_path(target)
        except ValueError as exc:
            return f"Error: {exc}"

        backend = mw._get_backend(runtime)
        if stages_in_cloud(backend):
            if not (
                validated.startswith(DOCUMENTS_ROOT + "/")
                or validated == DOCUMENTS_ROOT
            ):
                return (
                    "Error: cloud mkdir must target a path under /documents/ "
                    f"(got '{validated}')."
                )
            return Command(
                update={
                    "staged_dirs": [validated],
                    "staged_dir_tool_calls": {
                        validated: runtime.tool_call_id,
                    },
                    "messages": [
                        ToolMessage(
                            content=(
                                f"Staged directory '{validated}' (will be created "
                                "at end of turn)."
                            ),
                            tool_call_id=runtime.tool_call_id,
                        )
                    ],
                }
            )

        local_method = getattr(backend, "amkdir", None) or getattr(
            backend, "mkdir", None
        )
        if callable(local_method):
            try:
                res: Any = local_method(validated, parents=True, exist_ok=True)
                if asyncio.iscoroutine(res):
                    res = await res
            except TypeError:
                res = local_method(validated)
                if asyncio.iscoroutine(res):
                    res = await res
            except Exception as exc:  # pragma: no cover
                return f"Error: {exc}"
            error = getattr(res, "error", None)
            if error:
                return error
        return f"Created directory {validated}"

    return build_tool(
        name="mkdir",
        description=description,
        coroutine=async_mkdir,
        metadata={
            "activity_descriptor": ActivityDescriptor(
                active_title="Creating folder",
                completed_title="Created folder",
                category="file",
                icon_key="folder-plus",
                kind="mkdir",
            ).as_metadata()
        },
    )
