"""The git-native persistence middleware only builds for flagged workspaces."""

from __future__ import annotations

import pytest

from app.agents.chat.multi_agent_chat.main_agent.middleware.knowledge_store_persistence import (
    KnowledgeStorePersistenceMiddleware,
    build_knowledge_store_persistence_mw,
)

pytestmark = pytest.mark.unit


def _build(
    *, knowledge_store_enabled: bool
) -> KnowledgeStorePersistenceMiddleware | None:
    return build_knowledge_store_persistence_mw(
        workspace_id=7,
        user_id="1",
        thread_id=42,
        llm=object(),
        knowledge_store_enabled=knowledge_store_enabled,
    )


def test_builds_for_flagged_workspaces():
    assert isinstance(
        _build(knowledge_store_enabled=True),
        KnowledgeStorePersistenceMiddleware,
    )


def test_skipped_when_the_workspace_is_not_flipped():
    assert _build(knowledge_store_enabled=False) is None
