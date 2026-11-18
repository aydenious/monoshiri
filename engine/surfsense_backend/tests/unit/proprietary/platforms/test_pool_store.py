"""Pool-store catches only Redis/payload failures; other faults surface."""

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.proprietary.platforms.google_search import pool_store

pytestmark = pytest.mark.unit


class _Client:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def set(self, *_a, **_k):
        raise self._exc

    def delete(self, *_a, **_k):
        raise self._exc

    def scan_iter(self, *_a, **_k):
        raise self._exc


@pytest.fixture
def failing(monkeypatch: pytest.MonkeyPatch):
    def install(exc: Exception) -> None:
        monkeypatch.setattr(pool_store, "_get_client", lambda: _Client(exc))

    return install


def test_redis_error_is_swallowed(failing) -> None:
    failing(RedisConnectionError("down"))
    pool_store._publish_sync("p", [])
    pool_store._evict_sync("p")
    assert pool_store._adopt_sync(set()) is None


def test_unexpected_error_propagates(failing) -> None:
    failing(KeyError("bug"))
    with pytest.raises(KeyError):
        pool_store._publish_sync("p", [])
    with pytest.raises(KeyError):
        pool_store._evict_sync("p")
    with pytest.raises(KeyError):
        pool_store._adopt_sync(set())


def test_get_client_disables_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import redis

    def boom(*_a, **_k):
        raise RedisConnectionError("refused")

    monkeypatch.setattr(redis.Redis, "from_url", boom)
    monkeypatch.setattr(pool_store, "_client", None)
    monkeypatch.setattr(pool_store, "_disabled", False)
    assert pool_store._get_client() is None
    assert pool_store._disabled is True


def test_get_client_propagates_unexpected(monkeypatch: pytest.MonkeyPatch) -> None:
    import redis

    def boom(*_a, **_k):
        raise KeyError("bug")

    monkeypatch.setattr(redis.Redis, "from_url", boom)
    monkeypatch.setattr(pool_store, "_client", None)
    monkeypatch.setattr(pool_store, "_disabled", False)
    with pytest.raises(KeyError):
        pool_store._get_client()


def test_adopt_skips_unhashable_proxy_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Payload:
        def scan_iter(self, *_a, **_k):
            return iter(["k"])

        def get(self, _key):
            return '{"proxy": ["a"]}'

    monkeypatch.setattr(pool_store, "_get_client", lambda: _Payload())
    assert pool_store._adopt_sync(set()) is None
