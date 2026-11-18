"""Celery tasks that drive batched MinerU conversion.

``convert_document_step`` runs one page batch and re-enqueues itself; a Redis
lock per document guarantees that only one loop is alive for a document even
if two resumers race. ``resume_conversions`` sweeps the durable job directory
after a worker/host restart and on every worker start.

The heavy lifting lives in :mod:`app.conversion.runner` so it can be tested
without Celery or a broker.
"""

from __future__ import annotations

import logging
import math
import tempfile
import threading
import time
import uuid
from pathlib import Path

import redis
from celery.signals import worker_ready

from app.celery_app import celery_app
from app.config import config
from app.conversion.job_store import job_store_from_config
from app.tasks.celery_tasks import get_celery_session_maker, run_async_celery_task

logger = logging.getLogger(__name__)

LOCK_TTL_SECONDS = 300
LOCK_REFRESH_SECONDS = 60
SCHEDULE_GRACE_SECONDS = 120.0  # marker TTL = successor delay + this grace
LOCK_RETRY_SECONDS = 30
MAX_LOCK_RETRIES = 10  # ~5 min of contention, then the sweep recovers
HANDOFF_MAX_AGE_SECONDS = 24 * 3600

_redis_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """Shared sync Redis client (the app's existing heartbeat-style helper)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(config.REDIS_APP_URL, decode_responses=True)
    return _redis_client


def lock_key(document_id: int) -> str:
    return f"conversion:lock:{int(document_id)}"


def schedule_key(document_id: int) -> str:
    """Marker for a successor step that is queued but has not started yet."""
    return f"conversion:sched:{int(document_id)}"


def mark_scheduled(redis_client, document_id: int, delay: float) -> None:
    """Remember that a step for *document_id* is queued (sweep must skip it)."""
    ttl = math.ceil(max(0.0, float(delay)) + SCHEDULE_GRACE_SECONDS)
    redis_client.set(schedule_key(document_id), "1", ex=ttl)


def clear_scheduled(redis_client, document_id: int) -> None:
    """The step is starting now; its queued marker is no longer needed."""
    redis_client.delete(schedule_key(document_id))


def enqueue_step(document_id: int, delay: float = 0.0) -> None:
    """Re-enqueue a conversion step and record the schedule marker."""
    mark_scheduled(get_redis_client(), document_id, delay)
    convert_document_step.apply_async((document_id,), countdown=delay)


def remove_stale_handoffs(
    directory: str | None = None,
    *,
    now: float | None = None,
    max_age: float = HANDOFF_MAX_AGE_SECONDS,
) -> int:
    """Delete hand-off temp files left behind by a SIGKILL during indexing.

    The hand-off lives in the shared temp dir and is normally unlinked by
    ``_finish_conversion``; a hard kill between writing and unlinking leaves it
    behind, so the resume sweep reaps old ones.
    """
    from app.conversion.runner import HANDOFF_PREFIX

    root = Path(directory or tempfile.gettempdir())
    if not root.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - max_age
    removed = 0
    for entry in root.glob(f"{HANDOFF_PREFIX}*"):
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


class ConversionLock:
    """``SET NX EX`` lock with an owner token, refreshed from a daemon thread.

    The Celery task is synchronous, so the refresh runs in a thread rather than
    an asyncio task; both the refresh and the release are token-checked, so a
    lock that expired and was taken by another worker is never stolen back.
    """

    def __init__(
        self,
        client,
        document_id: int,
        *,
        ttl: int = LOCK_TTL_SECONDS,
        refresh: float = LOCK_REFRESH_SECONDS,
    ) -> None:
        self._client = client
        self._key = lock_key(document_id)
        self._ttl = ttl
        self._refresh = refresh
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.token = uuid.uuid4().hex

    @property
    def key(self) -> str:
        return self._key

    def acquire(self) -> bool:
        return bool(self._client.set(self._key, self.token, nx=True, ex=self._ttl))

    def refresh(self) -> bool:
        if self._client.get(self._key) != self.token:
            return False
        return bool(self._client.expire(self._key, self._ttl))

    def release(self) -> bool:
        """Stop refreshing, then delete the key iff we still own it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._client.get(self._key) != self.token:
            return False
        return bool(self._client.delete(self._key))

    def start_refresher(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._refresh_loop,
                name=f"conversion-lock-{self._key}",
                daemon=True,
            )
            self._thread.start()

    def _refresh_loop(self) -> None:
        while not self._stop.wait(self._refresh):
            try:
                self.refresh()
            except (redis.exceptions.RedisError, OSError):
                # a failed refresh just risks expiry, never raises
                logger.warning("conversion lock refresh failed: %s", self._key)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    name="convert_document_step",
    bind=True,
    acks_late=True,
    max_retries=MAX_LOCK_RETRIES,
)
def convert_document_step(self, document_id: int):
    """Convert one page batch for *document_id*, then re-enqueue if needed."""
    from app.conversion.runner import StepOutcome, run_step

    redis_client = get_redis_client()
    lock = ConversionLock(redis_client, document_id)
    if not lock.acquire():
        # Another worker owns this document; retry instead of acking and
        # dropping the message (M6), but do not retry forever.
        if self.request.retries < MAX_LOCK_RETRIES:
            logger.info(
                "conversion lock held for document %s; retrying in %ss",
                document_id,
                LOCK_RETRY_SECONDS,
            )
            raise self.retry(countdown=LOCK_RETRY_SECONDS)
        logger.warning(
            "conversion lock still held for document %s after %s retries; "
            "leaving it to the resume sweep",
            document_id,
            MAX_LOCK_RETRIES,
        )
        return

    try:
        clear_scheduled(redis_client, document_id)
        lock.start_refresher()
        outcome, delay = run_async_celery_task(
            lambda: run_step(
                document_id,
                store=job_store_from_config(),
                session_maker=get_celery_session_maker,
            )
        )
    finally:
        lock.release()

    if outcome in (StepOutcome.CONTINUE, StepOutcome.RETRY_LATER):
        # Re-enqueueing (rather than looping) puts the next batch behind any
        # small items already queued — a photo never waits for a whole book.
        # The marker keeps the resume sweep from adding a second chain (I1).
        enqueue_step(document_id, delay)


async def _resume_conversions(
    *,
    store=None,
    redis_client=None,
    session_maker=None,
    enqueue=None,
) -> None:
    """Requeue unfinished jobs; drop directories whose document is gone/done."""
    from app.conversion.job_store import CorruptJobError
    from app.conversion.runner import (
        CORRUPT_REASON,
        progress_status,
        read_document,
        write_status,
    )
    from app.db import DocumentStatus

    store = store if store is not None else job_store_from_config()
    redis_client = redis_client if redis_client is not None else get_redis_client()
    session_maker = session_maker or get_celery_session_maker
    enqueue = enqueue or (lambda document_id: enqueue_step(document_id, 0.0))

    # A killed create() can leave a staging directory; it is never a document.
    store.remove_staging()
    # A SIGKILL during indexing can leave the merged-Markdown hand-off behind.
    remove_stale_handoffs()

    for document_id in store.list_ids():
        document = await read_document(session_maker, document_id)
        state = DocumentStatus.get_state(document.status) if document else None
        if document is None or state in (DocumentStatus.READY, DocumentStatus.FAILED):
            store.delete(document_id)
            continue
        if state == "deleting":
            # The delete route owns both the row and the job directory; never
            # flip a document that is going away back to a live state.
            continue
        if redis_client.exists(lock_key(document_id)) or redis_client.exists(
            schedule_key(document_id)
        ):
            # A worker is on it, or a successor is queued: no second chain.
            continue
        try:
            job = store.load(document_id)
        except CorruptJobError:
            # Never crash-loop the sweep: fail the document with a reason.
            logger.warning(
                "corrupt conversion state for document %s", document_id, exc_info=True
            )
            await write_status(
                session_maker, document_id, DocumentStatus.failed(CORRUPT_REASON)
            )
            store.delete(document_id)
            continue
        if job is None:
            # A directory without job.json is a half-created orphan.
            store.delete(document_id)
            continue
        # Only a job we actually re-queue is stamped "queued", and a document
        # waiting on the converter keeps that stage (I2).
        stage = (
            document.status.get("stage") if isinstance(document.status, dict) else None
        )
        if stage != DocumentStatus.STAGE_WAITING:
            await write_status(
                session_maker,
                document_id,
                progress_status(job, DocumentStatus.STAGE_QUEUED),
            )
        enqueue(document_id)


@celery_app.task(name="resume_conversions")
def resume_conversions():
    """Beat entry: sweep the durable job directory every five minutes."""
    return run_async_celery_task(_resume_conversions)


@worker_ready.connect
def _resume_conversions_on_worker_ready(**_kwargs) -> None:
    """A restarted worker picks up whatever the last one left behind."""
    try:
        resume_conversions.apply_async()
    except Exception:  # never block worker start-up on the broker
        logger.exception("could not enqueue conversion resume sweep")
