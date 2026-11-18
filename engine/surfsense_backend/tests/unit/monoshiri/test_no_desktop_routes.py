"""Tripwire: the desktop-only HTTP surface and chat mode stay gone.

The Electron app is no longer part of Monoshiri. The web always sends
``filesystem_mode="cloud"``. Restore a route or mode only together with the
client that needs it, and delete this test in the same change.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.app import app
from app.schemas.new_chat import NewChatRequest

pytestmark = pytest.mark.unit

REMOVED_ROUTES = (
    "/documents/watched-folders",
    "/documents/folder-mtime-check",
    "/documents/folder-unlink",
    "/documents/folder-sync-finalize",
    "/folders/{folder_id}/watched",
    "/auth/desktop/login",
    "/auth/desktop/session",
)


def test_removed_desktop_routes_are_not_mounted() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    mounted = sorted(
        path
        for path in paths
        if any(path.endswith(suffix) for suffix in REMOVED_ROUTES)
    )
    assert not mounted, f"desktop-only routes are mounted again: {mounted}"


def test_chat_request_rejects_the_desktop_filesystem_mode() -> None:
    with pytest.raises(ValidationError):
        NewChatRequest(
            chat_id=1,
            workspace_id=1,
            user_query="hello",
            filesystem_mode="desktop_local_folder",
        )
