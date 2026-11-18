"""The irreversible half of deletion, run off the request."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import delete, select

from app.celery_app import celery_app
from app.db import User, Workspace
from app.tasks.celery_tasks import get_celery_session_maker, run_async_celery_task

logger = logging.getLogger(__name__)


@celery_app.task(
    name="erase_account",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=5,
)
def erase_account_task(self, user_id: str) -> None:
    """Erase an account that the API has already locked out."""
    return run_async_celery_task(lambda: erase_account(UUID(user_id)))


async def erase_account(user_id: UUID) -> None:
    """Erase the account and every workspace it owns, members and all.

    Safe to run twice: a retry after a partial run finishes the rest.
    """
    async with get_celery_session_maker()() as session:
        owned = (
            await session.scalars(
                select(Workspace.id).where(Workspace.user_id == user_id)
            )
        ).all()

    # Not the raw cascade: it would drop the rows while leaving their blobs and
    # knowledge stores on disk forever.
    from app.tasks.celery_tasks.document_tasks import _delete_workspace_background

    for workspace_id in owned:
        await _delete_workspace_background(workspace_id)

    async with get_celery_session_maker()() as session:
        user = await session.get(User, user_id)
        if user is None:
            return

        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()

    logger.info("Erased account %s and %d workspace(s)", user_id, len(owned))
