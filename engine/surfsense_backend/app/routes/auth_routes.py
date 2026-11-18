"""Authentication routes for refresh token management."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi_users import exceptions as fastapi_users_exceptions
from fastapi_users.router.common import ErrorModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.auth.session_cookies import (
    access_expires_at,
    clear_session,
    issue,
    read_refresh,
)
from app.config import config
from app.db import User, async_session_maker, get_async_session
from app.rate_limiter import limiter
from app.schemas.auth import (
    LogoutAllResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    SessionResponse,
)
from app.users import (
    UserManager,
    get_auth_context,
    get_jwt_strategy,
    get_user_manager,
)
from app.utils.refresh_tokens import (
    revoke_all_user_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/jwt", tags=["auth"])
session_router = APIRouter(prefix="/auth", tags=["auth"])


async def _load_user(user_id) -> User | None:
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalars().first()


async def resolve_google_user(
    *,
    user_manager: UserManager,
    request: Request,
    google_access_token: str,
    claims: dict,
    expires_at: int | None = None,
    google_refresh_token: str | None = None,
) -> User:
    """Resolve a Google identity with one policy for every OAuth flow.

    Email-based account linking is only allowed when Google asserts that the
    email is verified. Existing OAuth accounts continue to resolve by provider
    account id regardless of the current email claim.
    """
    if not claims.get("sub") or not claims.get("email"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google identity token",
        )

    sub = claims["sub"]
    email_verified = bool(claims.get("email_verified"))

    canonical_user = await user_manager.user_db.get_by_oauth_account("google", sub)
    if canonical_user is None:
        legacy_account_id = f"people/{sub}"
        legacy_user = await user_manager.user_db.get_by_oauth_account(
            "google", legacy_account_id
        )
        if legacy_user is not None:
            # Fallback for pre-sub Google OAuth rows created by the old web flow.
            # TODO: Remove after oauth_account is fully backfilled to bare Google
            # sub and production has zero google rows with account_id LIKE 'people/%'.
            for oauth_account in legacy_user.oauth_accounts:
                if (
                    oauth_account.oauth_name == "google"
                    and oauth_account.account_id == legacy_account_id
                ):
                    await user_manager.user_db.update_oauth_account(
                        legacy_user,
                        oauth_account,
                        {"account_id": sub},
                    )
                    break

    try:
        return await user_manager.oauth_callback(
            "google",
            google_access_token,
            sub,
            claims["email"],
            expires_at=expires_at,
            refresh_token=google_refresh_token,
            request=request,
            associate_by_email=email_verified,
            is_verified_by_default=email_verified,
        )
    except fastapi_users_exceptions.UserAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAUTH_USER_ALREADY_EXISTS",
        ) from exc


@router.post(
    "/refresh",
    response_model=None,
    responses={
        status.HTTP_200_OK: {
            "model": RefreshTokenResponse,
            "description": (
                "Header mode (no refresh cookie). refresh_token is null during "
                "the access-only grace window (REFRESH_ROTATION_GRACE_SECONDS, "
                "default 45 s) after this refresh token was already rotated: "
                "keep your current refresh token. Cookie mode returns "
                "{authenticated, access_expires_at}."
            ),
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorModel},
    },
)
@limiter.limit("30/minute")
async def refresh_access_token(
    request: Request,
    response: Response,
    body: RefreshTokenRequest | None = None,
):
    """
    Exchange a valid refresh token for a new access token and refresh token.
    Implements token rotation for security.
    """
    refresh_token, mode = read_refresh(request, body)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    rotation = await rotate_refresh_token(refresh_token)
    if not rotation:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user = await _load_user(rotation.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    strategy = get_jwt_strategy()
    access_token = await strategy.write_token(user)

    logger.info(f"Refreshed token for user {user.id}")

    return issue(
        response,
        mode,
        access=access_token,
        refresh=rotation.refresh_token,
        access_expires_at=access_expires_at(access_token),
        request=request,
    )


@router.post("/revoke", response_model=LogoutResponse)
async def revoke_token(
    request: Request,
    response: Response,
    body: LogoutRequest | None = None,
):
    """
    Logout current device by revoking the provided refresh token.
    Does not require authentication - just the refresh token.
    """
    refresh_token, _mode = read_refresh(request, body)
    revoked = await revoke_refresh_token(refresh_token) if refresh_token else False
    clear_session(response, request)
    if revoked:
        logger.info("User logged out from current device - token revoked")
    else:
        logger.warning("Logout called but no matching token found to revoke")
    return LogoutResponse()


@router.post("/logout-all", response_model=LogoutAllResponse)
async def logout_all_devices(
    request: Request,
    response: Response,
    body: LogoutRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Logout from all devices by revoking all refresh tokens for the user.
    Requires valid access token.
    """
    user: User | None = None
    try:
        auth = await get_auth_context(
            request, session=session, user_manager=user_manager
        )
        if auth.is_session:
            user = auth.user
    except HTTPException:
        user = None

    if user is None:
        refresh_token, _mode = read_refresh(request, body)
        token_record = (
            await validate_refresh_token(refresh_token) if refresh_token else None
        )
        if token_record:
            user = await _load_user(token_record.user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    await revoke_all_user_tokens(user.id)
    clear_session(response, request)
    logger.info(f"User {user.id} logged out from all devices")
    return LogoutAllResponse()


@session_router.get("/session", response_model=SessionResponse)
async def get_session(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
):
    if auth.method == "pat":
        return SessionResponse(access_expires_at=None)

    access_token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if access_token is None:
        auth_header = request.headers.get("Authorization")
        if auth_header:
            scheme, _, token = auth_header.partition(" ")
            if scheme.lower() == "bearer" and token:
                access_token = token

    if access_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    return SessionResponse(access_expires_at=access_expires_at(access_token))
