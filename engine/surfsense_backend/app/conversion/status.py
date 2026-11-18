"""Liveness for long conversions: progress refreshes and a background keep-alive.

The REST layer derives ``health`` from the last refresh (see
:meth:`app.schemas.documents.DocumentStatusSchema.from_document`). Nothing here
invents new ``state`` values: a conversion that stops refreshing reads as
"stalled" purely because its ``updated_at`` went cold.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.utils.db_faults import DB_FAULTS

logger = logging.getLogger(__name__)

STALLED_AFTER = timedelta(minutes=15)
# The outage-wait path refreshes updated_at on every retry; retries back off to
# at most 10 minutes. A wait that has not been refreshed for this long is not a
# live wait any more, it is a stalled one.
MAX_OUTAGE_BACKOFF = timedelta(seconds=600)
WAITING_STALLED_AFTER = STALLED_AFTER + MAX_OUTAGE_BACKOFF

_TOUCH_SQL = text(
    """
    UPDATE documents
    SET status = jsonb_set(status, '{updated_at}', to_jsonb(CAST(:iso AS text)))
    WHERE id = :document_id
      AND status->>'state' IN ('pending', 'processing')
    """
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _celery_session_maker() -> async_sessionmaker:
    from app.tasks.celery_tasks import get_celery_session_maker

    return get_celery_session_maker()


async def touch_status(session: AsyncSession, document_id: int) -> None:
    """Refresh ``status.updated_at`` for an in-flight document, atomically."""
    await session.execute(
        _TOUCH_SQL, {"iso": utc_now_iso(), "document_id": document_id}
    )
    await session.commit()


@asynccontextmanager
async def status_keepalive(
    document_id: int,
    *,
    every: float = 60.0,
    session_maker: Callable[[], async_sessionmaker] | None = None,
) -> AsyncIterator[None]:
    """Refresh ``status.updated_at`` every *every* seconds until the block exits.

    The refresh runs in its own short-lived session so a long-running caller's
    transaction is never held open just to prove it is alive. Errors are logged
    and swallowed: losing a heartbeat must not abort the work being guarded.
    """
    make_session = session_maker or _celery_session_maker

    async def _loop() -> None:
        while True:
            await asyncio.sleep(every)
            try:
                async with make_session()() as session:
                    await touch_status(session, document_id)
            except asyncio.CancelledError:
                raise
            except DB_FAULTS:
                # Any database failure, including the raw ones raised while
                # connecting, must only cost the heartbeat.
                logger.warning(
                    "status keepalive for document %s failed",
                    document_id,
                    exc_info=True,
                )
            except Exception:
                # Anything else is a bug, but it must still only cost the heartbeat.
                logger.exception(
                    "status keepalive for document %s failed unexpectedly", document_id
                )

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
