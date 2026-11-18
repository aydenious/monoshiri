"""Token-in-body password login for native clients (iOS).

The web flow (``POST /auth/jwt/login``) answers with session cookies only, which
a native client cannot use. This route accepts the same OAuth2 password form and
returns the access + refresh pair in the body, so the app can keep them in the
Keychain and send ``Authorization: Bearer``. No cookies are set here.

Refresh stays ``POST /auth/jwt/refresh`` with a JSON ``refresh_token`` body; that
route already answers in header mode when no refresh cookie is present.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.router.common import ErrorCode, ErrorModel

from app.auth.password_login import issue_password_token_pair
from app.schemas.auth import TokenPair
from app.users import UserManager, get_user_manager

# Mounted by app.py under /auth/jwt next to the fastapi-users login router.
router = APIRouter(tags=["auth"])


@router.post(
    "/token",
    response_model=TokenPair,
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorModel,
            "content": {
                "application/json": {
                    "examples": {
                        ErrorCode.LOGIN_BAD_CREDENTIALS: {
                            "summary": "Bad credentials or the user is inactive.",
                            "value": {"detail": ErrorCode.LOGIN_BAD_CREDENTIALS},
                        },
                    }
                }
            },
        },
    },
)
async def token_login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    user_manager: UserManager = Depends(get_user_manager),
) -> TokenPair:
    """Email + password → access and refresh tokens in the body."""
    return await issue_password_token_pair(
        user_manager, form_data.username, form_data.password, request
    )
