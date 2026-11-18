import pytest

from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.backends.resolver import (
    build_backend_resolver,
)

pytestmark = pytest.mark.unit


class _RuntimeStub:
    state = {"files": {}}


def test_backend_resolver_falls_back_to_state_backend_without_workspace():
    resolver = build_backend_resolver()
    backend = resolver(_RuntimeStub())
    # When no workspace_id is provided we fall back to StateBackend so
    # sub-agents / tests without DB access still work.
    assert backend.__class__.__name__ == "StateBackend"


def test_backend_resolver_uses_kb_postgres_with_workspace():
    resolver = build_backend_resolver(workspace_id=42)
    backend = resolver(_RuntimeStub())
    assert backend.__class__.__name__ == "KBPostgresBackend"
    assert backend.workspace_id == 42


def test_backend_resolver_uses_git_tree_when_knowledge_store_enabled():
    # The caller resolves the per-workspace verdict; the resolver only obeys it.
    resolver = build_backend_resolver(workspace_id=42, knowledge_store_enabled=True)
    backend = resolver(_RuntimeStub())
    assert backend.__class__.__name__ == "GitTreeBackend"
    assert backend.workspace_id == 42
