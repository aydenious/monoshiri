"""Token-in-body login for native clients (iOS): POST /auth/jwt/token.

Refresh, rotation and the web cookie login are covered here too, because the
native flow depends on the existing refresh endpoint staying header/body based.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import config
from app.db import RefreshToken, User
from app.schemas.auth import RefreshTokenResponse

pytestmark = [
    pytest.mark.integration,
    # Needs real Postgres: run with `DB=1 scripts/backend-gate.sh tests/integration/monoshiri`.
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL (DB=1 backend-gate mode)",
    ),
]

TOKEN_PATH = "/auth/jwt/token"
REFRESH_PATH = "/auth/jwt/refresh"
PASSWORD = "Passw0rd!long"


@pytest.fixture(autouse=True)
def _isolated_login_rate_limit(monkeypatch):
    """Pin the login limiter to its in-memory fallback, freshly emptied.

    The limiter's Redis key is shared by every test in the run (and by the web
    login tests in test_admin_routes), so a test that asserts on ``429`` or
    simply logs in would otherwise inherit another test's counter.
    """
    import app.app as app_module

    def _redis_unavailable():
        raise OSError("redis unavailable in this test")

    monkeypatch.setattr(app_module, "_get_rate_limit_redis", _redis_unavailable)
    app_module._memory_rate_limits.clear()
    yield
    app_module._memory_rate_limits.clear()


async def _create_committed_user(async_engine, *, is_active: bool) -> User:
    """A committed account: refresh-token rows live outside the test transaction.

    ``create_refresh_token`` and ``rotate_refresh_token`` open their own session,
    so the user must be visible to a second connection (FK + is_active lookup).
    """
    from fastapi_users.password import PasswordHelper

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@surfsense.net",
        hashed_password=PasswordHelper().hash(PASSWORD),
        is_active=is_active,
        is_superuser=False,
        is_verified=True,
    )
    async with maker() as session:
        session.add(user)
        await session.commit()
    return user


async def _delete_committed_user(async_engine, user: User) -> None:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        await session.execute(
            delete(RefreshToken).where(RefreshToken.user_id == user.id)
        )
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest_asyncio.fixture
async def native_user(async_engine) -> AsyncGenerator[User, None]:
    user = await _create_committed_user(async_engine, is_active=True)
    try:
        yield user
    finally:
        await _delete_committed_user(async_engine, user)


@pytest_asyncio.fixture
async def inactive_user(async_engine) -> AsyncGenerator[User, None]:
    user = await _create_committed_user(async_engine, is_active=False)
    try:
        yield user
    finally:
        await _delete_committed_user(async_engine, user)


def _credentials(user: User) -> dict[str, str]:
    return {"username": user.email, "password": PASSWORD}


async def test_token_login_returns_pair_in_body_and_no_cookies(client, native_user):
    r = await client.post(TOKEN_PATH, data=_credentials(native_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "access_token",
        "refresh_token",
        "token_type",
        "access_expires_at",
    }
    assert body["token_type"] == "bearer"
    assert body["refresh_token"]
    assert body["access_expires_at"] > 0
    assert "set-cookie" not in r.headers

    me = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == native_user.email


async def test_token_login_bad_password_400(client, native_user):
    r = await client.post(
        TOKEN_PATH,
        data={"username": native_user.email, "password": "wrong-password"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "LOGIN_BAD_CREDENTIALS"
    assert "set-cookie" not in r.headers


async def test_token_login_inactive_user_400(client, inactive_user):
    """An inactive account gets the same generic 400 as bad credentials."""
    r = await client.post(TOKEN_PATH, data=_credentials(inactive_user))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "LOGIN_BAD_CREDENTIALS"
    assert "set-cookie" not in r.headers


async def test_token_login_blocked_by_registration_kill_switch(
    client, native_user, monkeypatch
):
    monkeypatch.setattr(config, "REGISTRATION_ENABLED", False)
    r = await client.post(TOKEN_PATH, data=_credentials(native_user))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Registration is disabled"


async def test_revoked_refresh_token_is_rejected(client, native_user):
    login = await client.post(TOKEN_PATH, data=_credentials(native_user))
    assert login.status_code == 200, login.text
    refresh_token = login.json()["refresh_token"]

    revoked = await client.post(
        "/auth/jwt/revoke", json={"refresh_token": refresh_token}
    )
    assert revoked.status_code == 200, revoked.text

    refreshed = await client.post(REFRESH_PATH, json={"refresh_token": refresh_token})
    assert refreshed.status_code == 401, refreshed.text


async def test_refresh_with_body_rotates(client, native_user, monkeypatch):
    login = await client.post(TOKEN_PATH, data=_credentials(native_user))
    assert login.status_code == 200, login.text
    first = login.json()

    refreshed = await client.post(
        REFRESH_PATH, json={"refresh_token": first["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    second = refreshed.json()
    assert second["access_token"]
    assert second["refresh_token"]
    assert second["refresh_token"] != first["refresh_token"]
    me = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {second['access_token']}"}
    )
    assert me.status_code == 200, me.text

    # Reusing a rotated token after the grace window is treated as theft: 401.
    monkeypatch.setattr(config, "REFRESH_ROTATION_GRACE_SECONDS", 0)
    reuse = await client.post(
        REFRESH_PATH, json={"refresh_token": first["refresh_token"]}
    )
    assert reuse.status_code == 401, reuse.text


async def test_refresh_grace_returns_access_only(client, native_user):
    """Reusing a rotated token inside the grace window yields no refresh token.

    The contract declares this as ``RefreshTokenResponse`` (nullable
    ``refresh_token``), not ``TokenPair``: the client must keep the token it
    already holds.
    """
    login = await client.post(TOKEN_PATH, data=_credentials(native_user))
    assert login.status_code == 200, login.text
    first = login.json()

    refreshed = await client.post(
        REFRESH_PATH, json={"refresh_token": first["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    second = refreshed.json()
    assert second["refresh_token"]
    assert second["refresh_token"] != first["refresh_token"]

    grace = await client.post(
        REFRESH_PATH, json={"refresh_token": first["refresh_token"]}
    )
    assert grace.status_code == 200, grace.text
    body = grace.json()
    assert body["refresh_token"] is None
    assert body["access_token"]
    parsed = RefreshTokenResponse.model_validate(grace.json())
    assert parsed.refresh_token is None


async def test_web_cookie_login_unchanged(client, native_user):
    r = await client.post("/auth/jwt/login", data=_credentials(native_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["authenticated"] is True
    assert body["access_expires_at"] > 0
    cookies = r.headers.get_list("set-cookie")
    assert any(config.SESSION_COOKIE_NAME in c for c in cookies), cookies
    assert any(config.REFRESH_COOKIE_NAME in c for c in cookies), cookies


async def test_token_login_rate_limited(client, native_user):
    statuses = []
    for _ in range(6):
        r = await client.post(TOKEN_PATH, data=_credentials(native_user))
        statuses.append(r.status_code)
    assert statuses[:5] == [200] * 5, statuses
    assert statuses[5] == 429, statuses
