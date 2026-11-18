"""Product analytics shim (Monoshiri: telemetry removed).

Monoshiri ships no telemetry. This module keeps the public names the rest of
the backend calls so call sites need no changes; every function is a silent
no-op that does no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.auth.context import AuthContext


def is_enabled() -> bool:
    """Always False: analytics are permanently disabled."""
    return False


def get_client() -> Any | None:
    """Always None: there is no analytics client."""
    return None


def capture(
    event: str,
    *,
    distinct_id: str,
    properties: dict[str, Any] | None = None,
    groups: dict[str, str] | None = None,
) -> None:
    """No-op."""
    return None


def capture_for(
    auth: AuthContext,
    event: str,
    properties: dict[str, Any] | None = None,
    groups: dict[str, str] | None = None,
) -> None:
    """No-op."""
    return None


def group_identify(
    group_type: str,
    group_key: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """No-op."""
    return None


def shutdown() -> None:
    """No-op."""
    return None


__all__ = [
    "capture",
    "capture_for",
    "get_client",
    "group_identify",
    "is_enabled",
    "shutdown",
]
