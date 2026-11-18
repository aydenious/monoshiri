"""Password → token-pair login for native clients.

``POST /auth/jwt/token`` parses the OAuth2 password form; everything after
that — authentication, the generic bad-credentials error, token minting and
the ``on_after_login`` hook — lives here.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException, Request, status
from fastapi_users.router.common import ErrorCode

from app.auth.session_cookies import access_expires_at
from app.schemas.auth import TokenPair
from app.users import UserManager, get_jwt_strategy
from app.utils.refresh_tokens import create_refresh_token


async def issue_password_token_pair(
    user_manager: UserManager,
    username: str,
    password: str,
    request: Request,
) -> TokenPair:
    """Authenticate credentials and mint an access + refresh pair.

    Raises the same generic 400 as the fastapi-users login for an unknown or
    inactive user, so the route cannot be used to enumerate accounts. No
    cookies are set; the pair goes in the body.
    """
    credentials = SimpleNamespace(username=username, password=password)
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorCode.LOGIN_BAD_CREDENTIALS,
        )

    access_token = await get_jwt_strategy().write_token(user)
    refresh_token = await create_refresh_token(user.id)
    await user_manager.on_after_login(user, request, None)

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_expires_at(access_token),
    )
