"""Admin user management: invites, deactivate/activate, delete (superuser only)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import config
from app.db import RefreshToken, User

pytestmark = [
    pytest.mark.integration,
    # Needs real Postgres: run with `DB=1 scripts/backend-gate.sh tests/integration/monoshiri`.
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL (DB=1 backend-gate mode)",
    ),
]

BASE = "/api/v1/admin"


@pytest.fixture
def enqueue_spy(monkeypatch) -> list[str]:
    import app.monoshiri.admin_routes as admin_routes

    enqueued: list[str] = []
    monkeypatch.setattr(
        admin_routes.erase_account_task, "delay", lambda uid: enqueued.append(uid)
    )
    return enqueued


async def test_admin_routes_require_superuser(client, make_user, auth, admin):
    normal = await make_user()
    target = await make_user()
    h = await auth(normal)
    calls = [
        ("GET", f"{BASE}/users", None),
        ("POST", f"{BASE}/invites", {}),
        ("GET", f"{BASE}/invites", None),
        ("DELETE", f"{BASE}/invites/abc", None),
        ("POST", f"{BASE}/users/{target.id}/deactivate", None),
        ("POST", f"{BASE}/users/{target.id}/activate", None),
        ("DELETE", f"{BASE}/users/{target.id}", {"confirm_email": target.email}),
    ]
    for method, path, body in calls:
        r = await client.request(method, path, json=body, headers=h)
        assert r.status_code == 403, (method, path, r.status_code)
        # no credentials at all is rejected too
        r = await client.request(method, path, json=body)
        assert r.status_code == 401, (method, path, r.status_code)


async def test_create_and_list_invite(client, admin, auth, monkeypatch):
    monkeypatch.setattr(config, "NEXT_FRONTEND_URL", "https://kb.example.org/")
    h = await auth(admin)
    r = await client.post(
        f"{BASE}/invites", json={"note": "for X", "days": 3}, headers=h
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("https://kb.example.org/register?invite=")
    listed = (await client.get(f"{BASE}/invites", headers=h)).json()
    assert [i["jti"] for i in listed] == [body["jti"]]
    assert listed[0]["note"] == "for X"
    assert listed[0]["created_by"] == admin.email
    # days is bounded 1..30
    for days in (0, 31):
        bad = await client.post(f"{BASE}/invites", json={"days": days}, headers=h)
        assert bad.status_code == 422


async def test_revoked_invite_cannot_register(client, admin, auth, monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_OPEN", False)
    monkeypatch.setattr(config, "NEXT_FRONTEND_URL", "https://kb.example.org")
    h = await auth(admin)
    body = (await client.post(f"{BASE}/invites", json={}, headers=h)).json()
    token = body["url"].split("invite=", 1)[1]

    assert (
        await client.delete(f"{BASE}/invites/{body['jti']}", headers=h)
    ).status_code == 204
    # idempotent, and unknown jtis are fine too
    assert (
        await client.delete(f"{BASE}/invites/{body['jti']}", headers=h)
    ).status_code == 204
    assert (
        await client.delete(f"{BASE}/invites/{'b' * 32}", headers=h)
    ).status_code == 204
    assert (await client.get(f"{BASE}/invites", headers=h)).json() == []

    r = await client.post(
        "/auth/register",
        params={"invite": token},
        json={"email": "new@example.org", "password": "Passw0rd!long"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "Signup is invite-only"


def _hash(password: str) -> str:
    from fastapi_users.password import PasswordHelper

    return PasswordHelper().hash(password)


async def test_deactivate_blocks_existing_token(
    client, admin, auth, make_user, db_session
):
    victim = await make_user()
    victim_headers = await auth(victim)
    assert (await client.get("/users/me", headers=victim_headers)).status_code == 200
    db_session.add(
        RefreshToken(
            user_id=victim.id,
            token_hash="a" * 64,
            expires_at=datetime.now(UTC),
            family_id=uuid.uuid4(),
        )
    )
    await db_session.flush()

    h = await auth(admin)
    r = await client.post(f"{BASE}/users/{victim.id}/deactivate", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False

    assert (await client.get("/users/me", headers=victim_headers)).status_code == 401
    left = await db_session.scalars(
        select(RefreshToken).where(RefreshToken.user_id == victim.id)
    )
    assert left.all() == []


async def test_deactivated_user_cannot_login(
    client, admin, auth, make_user, db_session
):
    user = await make_user()
    user.hashed_password = _hash("Passw0rd!long")
    await db_session.flush()
    login = {"username": user.email, "password": "Passw0rd!long"}
    h = await auth(admin)

    assert (
        await client.post(f"{BASE}/users/{user.id}/deactivate", headers=h)
    ).status_code == 200
    r = await client.post("/auth/jwt/login", data=login)
    assert r.status_code == 400, r.text


async def test_reactivate_restores_login(
    client, admin, auth, make_user, db_session, monkeypatch
):
    async def fake_refresh_token(user_id):
        # the real one writes via its own session, which can't see the uncommitted test user
        return "refresh-token"

    monkeypatch.setattr("app.users.create_refresh_token", fake_refresh_token)
    user = await make_user()
    user.hashed_password = _hash("Passw0rd!long")
    await db_session.flush()
    login = {"username": user.email, "password": "Passw0rd!long"}
    h = await auth(admin)

    await client.post(f"{BASE}/users/{user.id}/deactivate", headers=h)
    assert (await client.post("/auth/jwt/login", data=login)).status_code == 400
    r = await client.post(f"{BASE}/users/{user.id}/activate", headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert (await client.post("/auth/jwt/login", data=login)).status_code == 200


async def test_delete_requires_matching_confirm_email(
    client, admin, auth, make_user, db_session, enqueue_spy
):
    victim = await make_user()
    h = await auth(admin)
    path = f"{BASE}/users/{victim.id}"

    r = await client.request(
        "DELETE", path, json={"confirm_email": "wrong@x.org"}, headers=h
    )
    assert r.status_code == 400
    r = await client.request("DELETE", path, json={}, headers=h)
    assert r.status_code in (400, 422)
    assert enqueue_spy == []

    r = await client.request(
        "DELETE", path, json={"confirm_email": victim.email}, headers=h
    )
    assert r.status_code == 202, r.text
    assert enqueue_spy == [str(victim.id)]
    assert (await db_session.get(User, victim.id)).is_active is False


async def test_admin_cannot_modify_self(client, admin, auth, enqueue_spy):
    h = await auth(admin)
    for method, path, body in [
        ("POST", f"{BASE}/users/{admin.id}/deactivate", None),
        ("DELETE", f"{BASE}/users/{admin.id}", {"confirm_email": admin.email}),
    ]:
        r = await client.request(method, path, json=body, headers=h)
        assert r.status_code == 409, (method, r.status_code)
        assert r.json()["detail"] == "Cannot modify your own account here"
    assert enqueue_spy == []


async def test_list_users(client, admin, auth, make_user):
    other = await make_user()
    r = await client.get(f"{BASE}/users", headers=await auth(admin))
    assert r.status_code == 200
    rows = {u["email"]: u for u in r.json()}
    assert rows[other.email]["is_active"] is True
    assert rows[admin.email]["is_superuser"] is True
    assert {"id", "display_name", "created_at", "last_login_at"} <= rows[
        other.email
    ].keys()


async def test_failed_erase_dispatch_keeps_user_deactivated(
    client, admin, auth, make_user, db_session, monkeypatch
):
    import app.monoshiri.admin_routes as admin_routes

    def boom(_uid):
        raise RuntimeError("broker down")

    monkeypatch.setattr(admin_routes.erase_account_task, "delay", boom)
    victim = await make_user()
    h = await auth(admin)
    await client.post(f"{BASE}/users/{victim.id}/deactivate", headers=h)

    r = await client.request(
        "DELETE",
        f"{BASE}/users/{victim.id}",
        json={"confirm_email": victim.email},
        headers=h,
    )
    assert r.status_code == 503
    assert (await db_session.get(User, victim.id)).is_active is False


async def test_admin_cannot_modify_another_admin(
    client, admin, auth, make_user, db_session, enqueue_spy
):
    other = await make_user()
    other.is_superuser = True
    await db_session.flush()
    h = await auth(admin)
    for method, path, body in [
        ("POST", f"{BASE}/users/{other.id}/deactivate", None),
        ("POST", f"{BASE}/users/{other.id}/activate", None),
        ("DELETE", f"{BASE}/users/{other.id}", {"confirm_email": other.email}),
    ]:
        r = await client.request(method, path, json=body, headers=h)
        assert r.status_code == 409, (method, path)
        assert r.json()["detail"] == "Cannot modify another admin here"
    assert enqueue_spy == []
    assert (await db_session.get(User, other.id)).is_active is True


async def test_revoke_rejects_malformed_jti(client, admin, auth, fake_redis):
    h = await auth(admin)
    for bad in ("used:abc", "ZZ", "a" * 31, "A" * 32):
        r = await client.delete(f"{BASE}/invites/{bad}", headers=h)
        assert r.status_code == 404, bad
    assert (
        await client.delete(f"{BASE}/invites/{'a' * 32}", headers=h)
    ).status_code == 204


async def test_refresh_rotation_refuses_inactive_user(async_engine, monkeypatch):
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.utils.refresh_tokens as rt

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(rt, "async_session_maker", maker)
    uid = uuid.uuid4()
    async with maker() as s:
        s.add(
            User(
                id=uid,
                email=f"{uid.hex[:8]}@x.org",
                hashed_password="h",
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
        )
        await s.commit()
    try:
        token = rt.generate_refresh_token()
        async with maker() as s:
            s.add(
                RefreshToken(
                    user_id=uid,
                    token_hash=rt.hash_token(token),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                    family_id=uuid.uuid4(),
                )
            )
            await s.commit()
            await s.execute(
                User.__table__.update().where(User.id == uid).values(is_active=False)
            )
            await s.commit()
        assert await rt.rotate_refresh_token(token) is None
    finally:
        async with maker() as s:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()
