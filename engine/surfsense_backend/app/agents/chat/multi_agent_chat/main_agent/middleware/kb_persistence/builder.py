"""Commit staged cloud filesystem mutations to Postgres at end of turn."""

from __future__ import annotations

from .middleware import (
    KnowledgeBasePersistenceMiddleware,
)


def build_kb_persistence_mw(
    *,
    workspace_id: int,
    user_id: str | None,
    thread_id: int | None,
) -> KnowledgeBasePersistenceMiddleware:
    return KnowledgeBasePersistenceMiddleware(
        workspace_id=workspace_id,
        created_by_id=user_id,
        thread_id=thread_id,
    )
