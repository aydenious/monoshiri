"""Public composition factory for the filesystem middleware."""

from __future__ import annotations

from typing import Any

from .middleware import SurfSenseFilesystemMiddleware


def build_filesystem_mw(
    *,
    backend_resolver: Any,
    workspace_id: int,
    user_id: str | None,
    thread_id: int | None,
    read_only: bool = False,
    knowledge_store_enabled: bool = False,
) -> SurfSenseFilesystemMiddleware:
    return SurfSenseFilesystemMiddleware(
        backend=backend_resolver,
        workspace_id=workspace_id,
        created_by_id=user_id,
        thread_id=thread_id,
        read_only=read_only,
        knowledge_store_enabled=knowledge_store_enabled,
    )
