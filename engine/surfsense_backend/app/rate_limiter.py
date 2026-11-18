"""Shared SlowAPI limiter instance used by app.py and route modules."""

from __future__ import annotations

from slowapi import Limiter
from starlette.requests import Request

from app.config import config


def get_real_client_ip(request: Request) -> str:
    """Client IP for rate limiting, not spoofable by the client.

    CF-Connecting-IP / X-Real-IP are never read (the public proxy passes them
    through untouched). The reverse proxy appends the real peer as the LAST
    X-Forwarded-For entry, so use that; a client can only forge leading entries.
    Falls back to the socket peer.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "127.0.0.1"


_DEFAULT_LIMIT = "1024/minute"

limiter = Limiter(
    key_func=get_real_client_ip,
    storage_uri=config.REDIS_APP_URL,
    default_limits=[_DEFAULT_LIMIT],
    in_memory_fallback_enabled=True,
    in_memory_fallback=[_DEFAULT_LIMIT],
)
