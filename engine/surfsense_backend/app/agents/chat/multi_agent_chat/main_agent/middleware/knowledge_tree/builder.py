"""<workspace_tree> injection."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from .middleware import KnowledgeTreeMiddleware


def build_knowledge_tree_mw(
    *,
    workspace_id: int,
    llm: BaseChatModel,
) -> KnowledgeTreeMiddleware:
    return KnowledgeTreeMiddleware(
        workspace_id=workspace_id,
        llm=llm,
        inject_system_message=False,
    )
