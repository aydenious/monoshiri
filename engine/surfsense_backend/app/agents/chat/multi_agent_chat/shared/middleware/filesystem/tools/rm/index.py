"""``rm`` factory: resolve + validate the path, then stage or delete directly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool

from app.capabilities.core import ActivityDescriptor

from .._shared import build_delete_tool
from .description import select_description
from .helpers import cloud_rm, direct_rm

if TYPE_CHECKING:
    from ...middleware import SurfSenseFilesystemMiddleware


def create_rm_tool(mw: SurfSenseFilesystemMiddleware) -> BaseTool:
    return build_delete_tool(
        mw,
        name="rm",
        description=select_description(),
        path_doc="Absolute or relative path to the file to delete.",
        cloud=cloud_rm,
        direct=direct_rm,
        metadata={
            "activity_descriptor": ActivityDescriptor(
                active_title="Deleting file",
                completed_title="Deleted file",
                category="file",
                icon_key="file-x",
                kind="rm",
            ).as_metadata()
        },
    )
