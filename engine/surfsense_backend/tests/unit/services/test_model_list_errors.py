"""The model-list fetchers handle their known failures and let bugs surface."""

from __future__ import annotations

import httpx
import pytest

from app.services import model_list_service

pytestmark = pytest.mark.unit


class _FailingClient:
    def __init__(self, exc: BaseException):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, _url):
        raise self._exc


async def test_fetch_from_openrouter_handles_transport_error(monkeypatch) -> None:
    monkeypatch.setattr(
        model_list_service.httpx,
        "AsyncClient",
        lambda **_k: _FailingClient(httpx.ConnectError("down")),
    )
    assert await model_list_service._fetch_from_openrouter() is None


async def test_fetch_from_openrouter_propagates_unexpected_error(monkeypatch) -> None:
    monkeypatch.setattr(
        model_list_service.httpx,
        "AsyncClient",
        lambda **_k: _FailingClient(RuntimeError("bug")),
    )
    with pytest.raises(RuntimeError):
        await model_list_service._fetch_from_openrouter()


def test_load_fallback_handles_missing_and_corrupt_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(model_list_service, "FALLBACK_FILE", tmp_path / "missing.json")
    assert model_list_service._load_fallback() == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr(model_list_service, "FALLBACK_FILE", bad)
    assert model_list_service._load_fallback() == []
