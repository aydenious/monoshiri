"""``rmdir`` factory: resolve + validate the path, then stage or delete directly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool

from app.capabilities.core import ActivityDescriptor

from .._shared import build_delete_tool
from .description import select_description
from .helpers import cloud_rmdir, direct_rmdir

if TYPE_CHECKING:
    from ...middleware import SurfSenseFilesystemMiddleware


def create_rmdir_tool(mw: SurfSenseFilesystemMiddleware) -> BaseTool:
    return build_delete_tool(
        mw,
        name="rmdir",
        description=select_description(),
        path_doc="Absolute or relative path of the empty directory to delete.",
        cloud=cloud_rmdir,
        direct=direct_rmdir,
        metadata={
            "activity_descriptor": ActivityDescriptor(
                active_title="Deleting folder",
                completed_title="Deleted folder",
                category="file",
                icon_key="folder-x",
                kind="rmdir",
            ).as_metadata()
        },
    )
