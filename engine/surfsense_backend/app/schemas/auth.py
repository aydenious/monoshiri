"""Authentication schemas for refresh token endpoints."""

from typing import Literal

from pydantic import BaseModel


class RefreshTokenRequest(BaseModel):
    """Request body for token refresh endpoint."""

    refresh_token: str | None = None


class RefreshTokenResponse(BaseModel):
    """Response from token refresh endpoint.

    ``refresh_token`` is null during the access-only grace window: keep the
    current refresh token.
    """

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    access_expires_at: int


class TokenPair(BaseModel):
    """Tokens a native client stores; mirrors the header-mode refresh response."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: int


class LogoutRequest(BaseModel):
    """Request body for logout endpoint (current device)."""

    refresh_token: str | None = None


class LogoutResponse(BaseModel):
    """Response from logout endpoint (current device)."""

    detail: str = "Successfully logged out"


class LogoutAllResponse(BaseModel):
    """Response from logout all devices endpoint."""

    detail: str = "Successfully logged out from all devices"


class SessionResponse(BaseModel):
    authenticated: bool = True
    access_expires_at: int | None = None
