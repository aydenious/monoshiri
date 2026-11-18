"""Admin user management: invite links, list/deactivate/activate/delete users.

Every route requires an interactive *session* principal (cookie or bearer, never a
personal access token) whose user is a superuser. No DDL: this only uses the stock
fastapi-users columns, ``refresh_tokens`` and Redis (invite metadata).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.account_deletion.erase import erase_account_task
from app.auth.context import AuthContext
from app.config import config
from app.db import RefreshToken, User, get_async_session
from app.monoshiri.invites import (
    JTI_RE,
    create_invite,
    get_redis,
    list_invites,
    revoke_invite,
    store_invite_meta,
)
from app.users import require_session_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

SELF_MODIFY_DETAIL = "Cannot modify your own account here"


async def current_superuser(
    auth: AuthContext = Depends(require_session_context),
) -> User:
    # fastapi-users' own current_user(superuser=True) only reads the Authorization
    # header; the web app authenticates with the session cookie, so resolve through
    # the unified context (which also rejects inactive users) and check the flag here.
    if not auth.user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return auth.user


def get_admin_redis():
    return get_redis()


class AdminUser(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    is_active: bool
    is_superuser: bool
    # The users table has no creation timestamp; kept in the contract for later.
    created_at: datetime | None = None
    last_login_at: datetime | None


class InviteCreate(BaseModel):
    note: str = Field("", max_length=200)
    days: int | None = Field(None, ge=1, le=30)


class InviteCreated(BaseModel):
    url: str
    jti: str
    expires_at: datetime


class InviteListItem(BaseModel):
    jti: str
    note: str
    created_by: str
    created_at: datetime
    expires_at: datetime


class DeleteUserBody(BaseModel):
    confirm_email: str


def _to_admin_user(user: User) -> AdminUser:
    return AdminUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        last_login_at=user.last_login,
    )


async def _target(session: AsyncSession, admin: User, user_id: uuid.UUID) -> User:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=SELF_MODIFY_DETAIL
        )
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot modify another admin here",
        )
    return user


@router.get("/users", response_model=list[AdminUser])
async def list_users(
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    rows = await session.scalars(select(User).order_by(User.email))
    return [_to_admin_user(u) for u in rows.all()]


@router.post("/invites", response_model=InviteCreated)
async def create_signup_invite(
    body: InviteCreate,
    admin: User = Depends(current_superuser),
    redis=Depends(get_admin_redis),
):
    base = (config.NEXT_FRONTEND_URL or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NEXT_FRONTEND_URL is not configured",
        )
    ttl = body.days * 86400 if body.days else None
    token = create_invite(ttl_seconds=ttl)
    info = await store_invite_meta(token, redis, note=body.note, created_by=admin.email)
    return InviteCreated(
        url=f"{base}/register?invite={token}", jti=info.jti, expires_at=info.expires_at
    )


@router.get("/invites", response_model=list[InviteListItem])
async def get_pending_invites(
    _: User = Depends(current_superuser), redis=Depends(get_admin_redis)
):
    return [InviteListItem(**vars(i)) for i in await list_invites(redis)]


@router.delete("/invites/{jti}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invite(
    jti: str, _: User = Depends(current_superuser), redis=Depends(get_admin_redis)
):
    if not JTI_RE.fullmatch(jti):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
        )
    await revoke_invite(jti, redis)


@router.post("/users/{user_id}/deactivate", response_model=AdminUser)
async def deactivate_user(
    user_id: uuid.UUID,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    user = await _target(session, admin, user_id)
    # Same transaction: is_active is checked on every request (the access token dies
    # with it) and the refresh tokens go so no new access token can be minted.
    user.is_active = False
    await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await session.commit()
    logger.info("Admin %s deactivated user %s", admin.id, user.id)
    return _to_admin_user(user)


@router.post("/users/{user_id}/activate", response_model=AdminUser)
async def activate_user(
    user_id: uuid.UUID,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    user = await _target(session, admin, user_id)
    user.is_active = True
    await session.commit()
    logger.info("Admin %s activated user %s", admin.id, user.id)
    return _to_admin_user(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_user(
    user_id: uuid.UUID,
    body: DeleteUserBody,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Lock the account out now and queue erasure (mirrors DELETE /users/me)."""
    user = await _target(session, admin, user_id)
    if body.confirm_email.strip().casefold() != user.email.casefold():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm_email does not match the user's email",
        )
    await session.execute(
        update(User).where(User.id == user.id).values(is_active=False)
    )
    await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await session.commit()

    try:
        erase_account_task.delay(str(user.id))
    except Exception as dispatch_error:
        # Stay deactivated (never re-enable: the target may have been inactive already).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User is deactivated but erasure could not be queued. Retry the delete.",
        ) from dispatch_error
    logger.info("Admin %s queued user %s for erasure", admin.id, user.id)
    return {"status": "queued"}
