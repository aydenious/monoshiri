"""``move_file`` factory: dispatches staged vs direct working-copy moves."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from deepagents.backends.protocol import WriteResult
from deepagents.backends.utils import validate_path
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from app.capabilities.core import ActivityDescriptor

from ...middleware.path_resolution import resolve_move_target_path
from .._shared import Runtime, build_tool, stages_in_cloud
from .description import select_description
from .helpers import cloud_move_file

if TYPE_CHECKING:
    from ...middleware import SurfSenseFilesystemMiddleware


def create_move_file_tool(mw: SurfSenseFilesystemMiddleware) -> BaseTool:
    description = select_description()

    async def async_move_file(
        source_path: Annotated[str, "Absolute or relative source path."],
        destination_path: Annotated[str, "Absolute or relative destination path."],
        runtime: Runtime,
        *,
        overwrite: Annotated[
            bool,
            "If True, replace existing destination. Cloud mode rejects True. Defaults to False.",
        ] = False,
    ) -> Command | str:
        if not source_path.strip() or not destination_path.strip():
            return "Error: source_path and destination_path are required."

        source = resolve_move_target_path(source_path, runtime)
        dest = resolve_move_target_path(destination_path, runtime)
        try:
            validated_source = validate_path(source)
            validated_dest = validate_path(dest)
        except ValueError as exc:
            return f"Error: {exc}"

        backend = mw._get_backend(runtime)
        if stages_in_cloud(backend):
            return await cloud_move_file(
                mw,
                runtime,
                validated_source,
                validated_dest,
                overwrite=overwrite,
            )

        res: WriteResult = await backend.amove(
            validated_source, validated_dest, overwrite=overwrite
        )
        if res.error:
            return res.error
        update: dict[str, Any] = {
            "messages": [
                ToolMessage(
                    content=f"Moved '{validated_source}' to '{res.path or validated_dest}'",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
        if res.files_update is not None:
            update["files"] = res.files_update
        return Command(update=update)

    return build_tool(
        name="move_file",
        description=description,
        coroutine=async_move_file,
        metadata={
            "activity_descriptor": ActivityDescriptor(
                active_title="Moving file",
                completed_title="Moved file",
                category="file",
                icon_key="files",
                kind="move_file",
            ).as_metadata()
        },
    )
