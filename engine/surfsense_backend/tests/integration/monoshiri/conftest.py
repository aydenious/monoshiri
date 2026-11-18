"""Fixtures for the admin routes: real Postgres session, dict-backed Redis, real JWTs."""

from __future__ import annotations

import fnmatch
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.app import app
from app.db import User, get_async_session
from app.monoshiri.admin_routes import get_admin_redis
from app.users import get_jwt_strategy


class FakeRedis:
    """The subset of redis.asyncio the invite code uses."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    async def expire(self, key, seconds):
        return True

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def exists(self, key):
        return int(key in self.kv or key in self.hashes)

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.hashes.pop(k, None)

    async def scan_iter(self, match="*"):
        for k in [*self.kv, *self.hashes]:
            if fnmatch.fnmatch(k, match):
                yield k


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    import app.app as app_module

    r = FakeRedis()
    monkeypatch.setattr(app_module, "_get_signup_redis", lambda: r)
    app.dependency_overrides[get_admin_redis] = lambda: r
    return r


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> AsyncGenerator[httpx.AsyncClient, None]:
    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_async_session] = override_session
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest_asyncio.fixture
async def admin(db_session: AsyncSession, make_user) -> User:
    user = await make_user()
    user.is_superuser = True
    await db_session.flush()
    return user


async def bearer(user: User) -> dict[str, str]:
    token = await get_jwt_strategy().write_token(user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth():
    return bearer
