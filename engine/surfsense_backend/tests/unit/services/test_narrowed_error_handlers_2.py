"""Second burn-down pass: narrowed handlers keep their fallbacks, others propagate."""

from __future__ import annotations

import logging
import shutil
from types import SimpleNamespace

import httpx
import pytest

from app.agents.chat.multi_agent_chat.main_agent.middleware.action_log import (
    middleware as action_log,
)
from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.backends.kb_postgres import (
    KBPostgresBackend,
)
from app.services import openrouter_integration_service as orsvc
from app.services.auto_model_pin_service import _to_uuid

pytestmark = pytest.mark.unit


def _patch_async_client(monkeypatch, response=None, exc=None):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            if exc is not None:
                raise exc
            return response

    monkeypatch.setattr(orsvc.httpx, "AsyncClient", _Client)


def _resp(payload):
    return httpx.Response(
        200, json=payload, request=httpx.Request("GET", "https://x.test")
    )


@pytest.mark.asyncio
async def test_openrouter_async_fetch_returns_none_on_http_error(monkeypatch):
    _patch_async_client(monkeypatch, exc=httpx.ConnectError("down"))
    assert await orsvc._fetch_models_async() is None


@pytest.mark.asyncio
async def test_openrouter_async_fetch_returns_none_on_non_object_body(monkeypatch):
    _patch_async_client(monkeypatch, response=_resp([1, 2]))
    assert await orsvc._fetch_models_async() is None


@pytest.mark.asyncio
async def test_openrouter_async_fetch_returns_none_on_non_json_body(monkeypatch):
    bad = httpx.Response(
        200, content=b"<html>", request=httpx.Request("GET", "https://x.test")
    )
    _patch_async_client(monkeypatch, response=bad)
    assert await orsvc._fetch_models_async() is None


@pytest.mark.asyncio
async def test_openrouter_async_fetch_lets_unexpected_errors_propagate(monkeypatch):
    _patch_async_client(monkeypatch, exc=KeyError("bug"))
    with pytest.raises(KeyError):
        await orsvc._fetch_models_async()


def test_to_uuid_bad_string_is_none_and_good_string_parses():
    assert _to_uuid("not-a-uuid") is None
    assert str(_to_uuid("12345678-1234-5678-1234-567812345678")).startswith("12345678")


def test_file_data_size_handles_malformed_content():
    assert KBPostgresBackend._file_data_size({"content": ["ab", "c"]}) == 4
    assert KBPostgresBackend._file_data_size({"content": [1, 2]}) == 0
    assert KBPostgresBackend._file_data_size({"content": 5}) == 0


def test_action_log_resolvers_fall_back_when_request_is_odd(caplog):
    class _Broken:
        @property
        def tool_call(self):
            raise TypeError("boom")

    with caplog.at_level(logging.WARNING):
        assert action_log._resolve_tool_name(_Broken()) == "unknown"
        assert action_log._resolve_args_dict(_Broken()) is None
        assert action_log._resolve_tool_call_id(_Broken()) is None
    assert "could not read the tool name" in caplog.text
    assert "could not read the tool call id" in caplog.text


def test_action_log_thread_id_and_args_payload_fallbacks():
    req = SimpleNamespace(
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "7"}}),
        tool_call={"args": {"a": 1}},
    )
    assert action_log._resolve_thread_id(req) == 7
    assert action_log._resolve_args_payload(req) == {"a": 1}


def test_action_log_args_payload_survives_circular_args():
    circular: dict = {}
    circular["self"] = circular
    req = SimpleNamespace(tool_call={"args": circular})
    assert "_repr" in action_log._resolve_args_payload(req)


def test_action_log_thread_id_falls_back_on_an_infinite_value():
    req = SimpleNamespace(
        runtime=SimpleNamespace(config={"configurable": {"thread_id": float("inf")}})
    )
    assert action_log._resolve_thread_id(req) is None


def test_action_log_args_payload_survives_a_str_that_raises():
    class _BadStr:
        def __str__(self):
            raise KeyError("boom")

        def __repr__(self):
            return "<bad>"

    req = SimpleNamespace(tool_call={"args": {"a": _BadStr()}})
    assert action_log._resolve_args_payload(req) == {"_repr": "{'a': <bad>}"}


@pytest.mark.asyncio
async def test_drop_workspace_logs_oserror_and_lets_other_errors_propagate(
    monkeypatch, caplog
):
    from app.knowledge_store.service import KnowledgeStore

    store = KnowledgeStore.__new__(KnowledgeStore)
    store._workspace_id = 1

    def _oserror(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(shutil, "rmtree", _oserror)
    with caplog.at_level(logging.WARNING):
        await store.drop_workspace()
    assert "Could not delete knowledge store directory" in caplog.text

    def _bug(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(shutil, "rmtree", _bug)
    with pytest.raises(RuntimeError):
        await store.drop_workspace()
