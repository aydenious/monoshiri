"""SearXNG fallback treats transport/JSON failures as a miss, not other faults."""

import httpx
import pytest

from app.proprietary.platforms.google_search import searxng

pytestmark = pytest.mark.unit


def _client_raising(exc: Exception):
    class _C:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, *_a, **_k):
            raise exc

    return _C


async def test_http_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _client_raising(httpx.ConnectError("x")))
    assert await searxng._fetch_json({}) is None


async def test_unexpected_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _client_raising(KeyError("bug")))
    with pytest.raises(KeyError):
        await searxng._fetch_json({})


async def test_missing_socks_support_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_socks(*_a, **_k):
        raise ImportError(
            "Using SOCKS proxy, but the 'socksio' package is not installed."
        )

    monkeypatch.setattr(httpx, "AsyncClient", no_socks)
    assert await searxng._fetch_json({}) is None
