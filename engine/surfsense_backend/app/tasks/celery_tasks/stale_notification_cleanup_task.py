"""Celery task to detect and mark stale notifications as failed.

This task runs periodically (every 5 minutes by default) to find notifications
that are stuck in "in_progress" status but don't have an active Redis heartbeat key.
These are marked as "failed" to prevent the frontend from showing a perpetual
"syncing" or "processing" state.

It handles **document_processing** notifications (manual file uploads,
YouTube videos, etc.) and cleans up documents stuck in pending/processing
state by the document_id in the notification metadata.

Detection mechanism:
- Active tasks set a Redis key with TTL (2 minutes) as a heartbeat
- A background coroutine refreshes the key every 60 seconds
- If the task/worker crashes, the Redis key expires automatically
- This cleanup task checks for in-progress notifications without a Redis heartbeat key
- Such notifications are marked as failed with O(1) batch UPDATE
- Associated documents are also marked as failed
"""

import contextlib
import json
import logging
from datetime import UTC, datetime

import redis
from sqlalchemy import and_, or_, text
from sqlalchemy.future import select

from app.celery_app import celery_app
from app.config import config
from app.db import Document, DocumentStatus
from app.notifications.persistence import Notification
from app.tasks.celery_tasks import get_celery_session_maker, run_async_celery_task

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None

STALE_PROCESSING_ERROR_MESSAGE = "Syncing was interrupted unexpectedly. Please retry."


def get_redis_client() -> redis.Redis:
    """Get or create Redis client for heartbeat checking."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(config.REDIS_APP_URL, decode_responses=True)
    return _redis_client


def _get_heartbeat_key(notification_id: int) -> str:
    """Generate Redis key for notification heartbeat."""
    return f"indexing:heartbeat:{notification_id}"


@celery_app.task(name="cleanup_stale_indexing_notifications")
def cleanup_stale_indexing_notifications_task():
    """
    Check for stale notifications and mark them as failed.

    Handles document_processing notifications (manual file uploads, YouTube
    videos, etc.). Detection: Redis heartbeat key with 2-min TTL. Missing key
    = stale task. Also marks associated pending/processing documents as
    failed.
    """

    return run_async_celery_task(_cleanup_stale_document_processing_notifications)


# ===== Document Processing Cleanup (FILE uploads, YouTube, etc.) =====


async def _cleanup_stale_document_processing_notifications():
    """Find and mark stale document processing notifications as failed.

    Uses the Redis heartbeat mechanism for document_processing notifications
    (FILE uploads, YouTube videos, etc.).

    For each stale notification that contains a document_id in its metadata,
    the associated document is also marked as failed.
    """
    async with get_celery_session_maker()() as session:
        try:
            # Find all in-progress document processing notifications
            result = await session.execute(
                select(
                    Notification.id,
                    Notification.notification_metadata,
                ).where(
                    and_(
                        Notification.type == "document_processing",
                        Notification.notification_metadata["status"].astext
                        == "in_progress",
                    )
                )
            )
            in_progress_rows = result.fetchall()

            if not in_progress_rows:
                logger.debug("No in-progress document processing notifications found")
                return

            # Check which ones are missing heartbeat keys in Redis
            redis_client = get_redis_client()
            stale_notification_ids = []
            stale_document_ids = []

            for row in in_progress_rows:
                notification_id = row[0]
                metadata = row[1]  # Full metadata dict
                heartbeat_key = _get_heartbeat_key(notification_id)
                if not redis_client.exists(heartbeat_key):
                    stale_notification_ids.append(notification_id)
                    # Extract document_id from metadata for document cleanup
                    if metadata and isinstance(metadata, dict):
                        doc_id = metadata.get("document_id")
                        if doc_id is not None:
                            with contextlib.suppress(ValueError, TypeError):
                                stale_document_ids.append(int(doc_id))

            if not stale_notification_ids:
                logger.debug(
                    f"All {len(in_progress_rows)} in-progress document processing "
                    "notifications have active Redis heartbeats"
                )
                return

            logger.warning(
                f"Found {len(stale_notification_ids)} stale document processing "
                f"notifications (no Redis heartbeat): {stale_notification_ids}"
            )

            # O(1) Batch UPDATE: Mark stale notifications as failed
            update_data = {
                "status": "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "error_message": STALE_PROCESSING_ERROR_MESSAGE,
                "processing_stage": "failed",
            }

            await session.execute(
                text("""
                    UPDATE notifications
                    SET metadata = metadata || CAST(:update_json AS jsonb),
                        title = 'Failed: ' || COALESCE(metadata->>'document_name', 'Document'),
                        message = :display_message
                    WHERE id = ANY(:ids)
                """),
                {
                    "update_json": json.dumps(update_data),
                    "display_message": STALE_PROCESSING_ERROR_MESSAGE,
                    "ids": stale_notification_ids,
                },
            )

            logger.info(
                f"Successfully marked {len(stale_notification_ids)} stale document "
                "processing notifications as failed"
            )

            # Clean up stuck documents by document_id from notification metadata
            if stale_document_ids:
                await _cleanup_stuck_non_connector_documents(
                    session, stale_document_ids
                )

            await session.commit()

        except Exception as e:
            logger.error(
                f"Error cleaning up stale document processing notifications: {e!s}",
                exc_info=True,
            )
            await session.rollback()


async def _cleanup_stuck_non_connector_documents(session, document_ids: list[int]):
    """
    Mark specific non-connector documents stuck in pending/processing as failed.

    These are documents (FILE uploads, YouTube, etc.) identified from stale
    notification metadata. Only documents that are still in pending/processing
    state are updated — already-completed documents are left untouched.

    Args:
        session: Database session
        document_ids: List of document IDs to check and potentially mark as failed
    """
    if not document_ids:
        return

    try:
        # Find which of these documents are actually stuck
        count_result = await session.execute(
            select(Document.id).where(
                and_(
                    Document.id.in_(document_ids),
                    or_(
                        Document.status["state"].astext == DocumentStatus.PENDING,
                        Document.status["state"].astext == DocumentStatus.PROCESSING,
                    ),
                )
            )
        )
        stuck_doc_ids = [row[0] for row in count_result.fetchall()]

        if not stuck_doc_ids:
            logger.debug(
                f"No stuck non-connector documents found for IDs: {document_ids}"
            )
            return

        logger.warning(
            f"Found {len(stuck_doc_ids)} stuck non-connector documents "
            f"(pending/processing): {stuck_doc_ids}"
        )

        failed_status = DocumentStatus.failed(STALE_PROCESSING_ERROR_MESSAGE)

        await session.execute(
            text("""
                UPDATE documents
                SET status = CAST(:failed_status AS jsonb),
                    updated_at = :now
                WHERE id = ANY(:doc_ids)
                  AND (
                      status->>'state' = :pending_state
                      OR status->>'state' = :processing_state
                  )
            """),
            {
                "failed_status": json.dumps(failed_status),
                "now": datetime.now(UTC),
                "doc_ids": stuck_doc_ids,
                "pending_state": DocumentStatus.PENDING,
                "processing_state": DocumentStatus.PROCESSING,
            },
        )

        logger.info(
            f"Successfully marked {len(stuck_doc_ids)} stuck non-connector "
            "documents as failed"
        )

    except Exception as e:
        logger.error(
            f"Error cleaning up stuck non-connector documents {document_ids}: {e!s}",
            exc_info=True,
        )
        # Don't raise — let the rest of the cleanup continue
