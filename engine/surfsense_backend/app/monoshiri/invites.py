"""Invite-only signup: signed one-time invite tokens (no DDL; use is tracked in Redis)."""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from app.config import config

INVITE_AUDIENCE = "monoshiri:invite"
_USED_KEY = "monoshiri:invite:used:"
_META_KEY = "monoshiri:invite:"  # hash per invite; the used-marker shares this prefix


JTI_RE = re.compile(r"[0-9a-f]{32}")  # uuid4().hex, as minted by create_invite


class InviteError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "invalid" | "expired" | "used"


@dataclass
class InviteInfo:
    jti: str
    note: str
    created_by: str
    created_at: datetime
    expires_at: datetime


_redis_client = None


def get_redis():
    """Shared async Redis client (app Redis) for invite state."""
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(
            config.REDIS_APP_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


def create_invite(ttl_seconds: int | None = None) -> str:
    """Mint a signed invite token. Pure and sync: it touches no Redis.

    The token carries no note or creator (those live only in the Redis hash).
    The CLI uses this alone, so CLI invites work but have no listable metadata.
    The admin API additionally calls ``store_invite_meta`` so the invite can be
    listed and revoked.
    """
    ttl = config.INVITE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    now = int(time.time())
    claims = {
        "aud": INVITE_AUDIENCE,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(claims, config.SECRET_KEY, algorithm="HS256")


def _info(jti: str, meta: dict) -> InviteInfo:
    return InviteInfo(
        jti=jti,
        note=meta.get("note", ""),
        created_by=meta.get("created_by", ""),
        created_at=datetime.fromtimestamp(int(meta["created_at"]), UTC),
        expires_at=datetime.fromtimestamp(int(meta["exp"]), UTC),
    )


async def store_invite_meta(
    token: str, redis, note: str = "", created_by: str = ""
) -> InviteInfo:
    """Record listing metadata for a freshly minted invite (hash expires with it)."""
    claims = jwt.decode(
        token,
        config.SECRET_KEY,
        algorithms=["HS256"],
        audience=INVITE_AUDIENCE,
        options={"verify_exp": False},
    )
    meta = {
        "note": note,
        "created_by": created_by,
        "created_at": str(claims["iat"]),
        "exp": str(claims["exp"]),
    }
    key = _META_KEY + claims["jti"]
    await redis.hset(key, mapping=meta)
    await redis.expire(key, max(math.ceil(claims["exp"] - time.time()) + 1, 1))
    return _info(claims["jti"], meta)


async def list_invites(redis) -> list[InviteInfo]:
    """Unused, unexpired invites that have metadata, oldest first."""
    now = time.time()
    out: list[InviteInfo] = []
    async for key in redis.scan_iter(match=_META_KEY + "*"):
        if key.startswith(_USED_KEY):
            continue
        jti = key[len(_META_KEY) :]
        meta = await redis.hgetall(key)
        if not meta or int(meta["exp"]) <= now:
            continue
        if await redis.exists(_USED_KEY + jti):
            continue
        out.append(_info(jti, meta))
    return sorted(out, key=lambda i: i.created_at)


async def revoke_invite(jti: str, redis) -> None:
    """Invalidate an invite. Idempotent; unknown jtis (e.g. CLI invites) are a no-op."""
    key = _META_KEY + jti
    meta = await redis.hgetall(key)
    if not meta:
        return
    remaining = max(math.ceil(int(meta["exp"]) - time.time()) + 1, 1)
    await redis.set(_USED_KEY + jti, "1", nx=True, ex=remaining)
    await redis.delete(key)


async def consume_invite(token: str, redis) -> None:
    """Verify the invite and mark it used. Raises InviteError."""
    try:
        claims = jwt.decode(
            token,
            config.SECRET_KEY,
            algorithms=["HS256"],
            audience=INVITE_AUDIENCE,
            options={"require": ["exp", "jti", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InviteError("expired") from exc
    except jwt.PyJWTError as exc:
        raise InviteError("invalid") from exc
    remaining = max(math.ceil(claims["exp"] - time.time()) + 1, 1)
    ok = await redis.set(_USED_KEY + claims["jti"], "1", nx=True, ex=remaining)
    if not ok:
        raise InviteError("used")
