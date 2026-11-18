"""``write_file`` factory: resolve target, enforce cloud namespace, dispatch to backend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from deepagents.backends.protocol import WriteResult
from deepagents.backends.utils import create_file_data, validate_path
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from app.capabilities.core import ActivityDescriptor

from ...middleware.namespace_policy import check_cloud_write_namespace
from ...middleware.path_resolution import resolve_write_target_path
from .._shared import Runtime, build_tool, stages_in_cloud
from .description import select_description

if TYPE_CHECKING:
    from ...middleware import SurfSenseFilesystemMiddleware


def create_write_file_tool(mw: SurfSenseFilesystemMiddleware) -> BaseTool:
    description = select_description()

    async def async_write_file(
        file_path: Annotated[
            str,
            "Absolute path where the file should be created. Relative paths resolve against the current cwd.",
        ],
        content: Annotated[str, "Text content to write to the file."],
        runtime: Runtime,
    ) -> Command | str:
        target = resolve_write_target_path(file_path, runtime)
        try:
            validated = validate_path(target)
        except ValueError as exc:
            return f"Error: {exc}"

        namespace_error = check_cloud_write_namespace(validated, runtime)
        if namespace_error:
            return namespace_error

        backend = mw._get_backend(runtime)
        res: WriteResult = await backend.awrite(validated, content)
        if res.error:
            return res.error

        path = res.path or validated
        files_update = res.files_update or {path: create_file_data(content)}
        update: dict[str, Any] = {
            "files": files_update,
            "messages": [
                ToolMessage(
                    content=f"Updated file {path}",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
        if stages_in_cloud(backend):
            update["dirty_paths"] = [path]
            update["dirty_path_tool_calls"] = {path: runtime.tool_call_id}
        return Command(update=update)

    return build_tool(
        name="write_file",
        description=description,
        coroutine=async_write_file,
        metadata={
            "activity_descriptor": ActivityDescriptor(
                active_title="Creating file",
                completed_title="Created file",
                category="file",
                icon_key="file-plus",
                kind="write_file",
            ).as_metadata()
        },
    )
