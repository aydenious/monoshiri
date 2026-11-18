"""Shared filesystem tool assembly: sync entry blocks on the coroutine, staging predicate."""

from __future__ import annotations

import pytest

from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.backends.git_tree import (
    GitTreeBackend,
)
from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.tools._shared import (
    build_tool,
    stages_in_cloud,
)

pytestmark = pytest.mark.unit


def test_stages_in_cloud_only_for_non_git_cloud_backends():
    assert stages_in_cloud(object()) is True
    assert stages_in_cloud(GitTreeBackend.__new__(GitTreeBackend)) is False


def test_build_tool_sync_and_async_share_one_coroutine():
    async def echo(text: str) -> str:
        """Echo."""
        return f"echo:{text}"

    tool = build_tool(
        name="echo",
        description="Echo text.",
        coroutine=echo,
        metadata={"activity_descriptor": {"kind": "echo"}},
    )

    assert tool.name == "echo"
    assert tool.description == "Echo text."
    assert set(tool.tool_call_schema.model_json_schema()["properties"]) == {"text"}
    assert tool.invoke({"text": "a"}) == "echo:a"
    assert tool.metadata == {"activity_descriptor": {"kind": "echo"}}


async def test_list_tree_async_mixin_runs_list_tree_with_its_arguments():
    from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.backends.local_folder import (
        ListTreeAsyncMixin,
    )

    class Backend(ListTreeAsyncMixin):
        def list_tree(self, path, **kwargs):
            return {"path": path, **kwargs}

    assert await Backend().alist_tree("/x", max_depth=2, include_files=False) == {
        "path": "/x",
        "max_depth": 2,
        "page_size": 500,
        "include_files": False,
        "include_dirs": True,
    }
