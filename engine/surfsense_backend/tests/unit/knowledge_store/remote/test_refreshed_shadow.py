"""``WorkspaceRemotes._refreshed_shadow``: mount prefix, session-bound store, refreshed shadow."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import app.knowledge_store as knowledge_store
from app.knowledge_store.remote import facade
from app.knowledge_store.remote.paths import full_name_from_url, mount

pytestmark = pytest.mark.unit


async def test_refreshes_the_shadow_and_returns_prefix_store_shadow(monkeypatch):
    session, calls = object(), []

    class _Store:
        def with_session(self, s):
            calls.append(("with_session", s))
            return ("store", s)

    class _KnowledgeStore:
        @staticmethod
        def for_workspace(workspace_id):
            calls.append(("for_workspace", workspace_id))
            return _Store()

    class _Shadow:
        def __init__(self, path):
            self.path = path

        def refresh(self, url, *, branch):
            calls.append(("refresh", self.path, url, branch))

    @contextmanager
    def _span(**kwargs):
        calls.append(("span", kwargs))
        yield

    monkeypatch.setattr(knowledge_store, "KnowledgeStore", _KnowledgeStore)
    monkeypatch.setattr(facade, "Shadow", _Shadow)
    monkeypatch.setattr(
        facade, "shadow_path", lambda ws, row_id: f"/shadow/{ws}/{row_id}"
    )
    monkeypatch.setattr(facade.ks_telemetry, "remote_shadow_span", _span)

    remotes = facade.WorkspaceRemotes(5, engine=None, session=session)
    spec = SimpleNamespace(
        provider="github",
        url="https://github.com/acme/notes.git",
        sourcepath="docs",
        branch="main",
    )

    prefix, store, shadow = await remotes._refreshed_shadow(spec, SimpleNamespace(id=7))

    assert prefix == mount(
        provider="github",
        full_name=full_name_from_url(spec.url),
        sourcepath="docs",
    )
    assert store == ("store", session)
    assert shadow.path == "/shadow/5/7"
    assert calls == [
        ("for_workspace", 5),
        ("with_session", session),
        ("span", {"workspace_id": 5, "operation": "refresh"}),
        ("refresh", "/shadow/5/7", spec.url, "main"),
    ]
