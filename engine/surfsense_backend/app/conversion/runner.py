"""Batched MinerU conversion: the upload hand-off and the per-batch step.

The upload pipeline records a durable conversion job
(``app.conversion.job_store``) and enqueues a Celery step. Each
:func:`run_step` call converts **at most one page batch**, so a 400-page book
never holds a worker for hours: between batches the queue gets its turn
(spec settled point 3). Progress lives in ``documents.status``; resume state
lives on disk.

Every database access here opens a short, self-contained session and commits
immediately. No document row lock is ever held across a MinerU HTTP call or a
batch wait, so the keep-alive that Task 4 runs on its own connection can never
block on (or be blocked by) the conversion loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import tempfile
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import config
from app.conversion.heic import heic_to_png
from app.conversion.job_store import (
    ConversionCapacityError,
    CorruptJobError,
    Job,
    JobGone,
    JobStore,
)
from app.conversion.mineru_client import (
    MinerUClient,
    MinerUError,
    MinerUTaskFailed,
    MinerUTaskNotFound,
    MinerUUnavailable,
    client_from_config,
)
from app.conversion.status import touch_status
from app.db import Document, DocumentStatus, DocumentType
from app.utils.db_faults import DB_FAULTS
from app.utils.file_extensions import MINERU_NATIVE_EXTENSIONS

logger = logging.getLogger(__name__)

CAPACITY_REASON = "Conversion queue is full (disk limit); try again later"
EMPTY_REASON = "MinerU found no text in this file"
GIVE_UP_REASON = "MinerU unreachable for 24 h"
CORRUPT_REASON = "Conversion state was lost; upload the file again"
STEP_ERROR_REASON = "Conversion failed unexpectedly; upload the file again"
START_ERROR_REASON = "Could not start conversion"
BATCH_TIMEOUT_REASON = "Converter stopped responding"
HANDOFF_PREFIX = "mineru-"

TOUCH_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 600.0
MAX_STEP_ERRORS = 5  # consecutive unexpected exceptions before the document fails
_HEIC_SUFFIXES = {".heic", ".heif"}


class StepOutcome(StrEnum):
    """What the Celery wrapper should do after one :func:`run_step` call."""

    CONTINUE = "continue"  # enqueue the next batch now
    RETRY_LATER = "retry_later"  # enqueue with a delay (MinerU unreachable)
    DONE = "done"  # merged and handed to indexing
    STOPPED = "stopped"  # failed, or the document is gone


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _suffix(filename: str) -> str:
    return PurePosixPath(filename).suffix.lower()


def source_name_for(filename: str) -> str:
    """Fixed on-disk/upload name for *filename* (never the user's name)."""
    suffix = _suffix(filename)
    if suffix == ".tif":
        # MinerU names its image suffixes ".tiff"; be explicit rather than guess.
        suffix = ".tiff"
    if suffix not in MINERU_NATIVE_EXTENSIONS:
        suffix = ""
    return f"source{suffix}"


def _sha256(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _unlink(path: str | Path) -> None:
    with contextlib.suppress(OSError):
        Path(path).unlink()


def _pdf_page_count(path: Path) -> int:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        # Many ebooks carry only an owner password and an empty user password;
        # every viewer opens them. Try the empty password first and reject only
        # a file that really needs a user password.
        try:
            decrypted = reader.decrypt("")
        except Exception as exc:
            raise ValueError("PDF is encrypted") from exc
        if not decrypted:
            raise ValueError("PDF is encrypted")
    return len(reader.pages)


def progress_status(job: Job, stage: str) -> dict:
    """``processing`` status with the progress fields known for this job.

    Non-paged inputs (photo, DOCX) report only state/stage/updated_at:
    ``pages_total`` is null by contract and a page counter would be noise.
    """
    if job.pages_total is None:
        return DocumentStatus.processing(stage=stage)
    return DocumentStatus.processing(
        stage=stage,
        pages_done=job.pages_done(),
        pages_total=job.pages_total,
    )


# ---------------------------------------------------------------------------
# Short database sessions (never held across I/O)
# ---------------------------------------------------------------------------


async def read_document(session_maker: Callable[[], Any], document_id: int):
    """Load a document in its own short session."""
    async with session_maker()() as session:
        return await session.get(Document, document_id)


async def write_status(
    session_maker: Callable[[], Any], document_id: int, status: dict
) -> bool:
    """Write ``documents.status`` in its own short transaction.

    The session exists only for this statement + commit; callers must never
    hold it while waiting on MinerU.

    A document that is being deleted is never overwritten: a late conversion
    write must not resurrect a row the delete route is removing.
    """
    from app.tasks.document_processors.base import get_current_timestamp

    async with session_maker()() as session:
        document = await session.get(Document, document_id)
        if document is None:
            return False
        if DocumentStatus.get_state(document.status) == "deleting":
            return False
        document.status = status
        document.updated_at = get_current_timestamp()
        await session.commit()
    return True


async def _fail(
    session_maker: Callable[[], Any], document_id: int, reason: str
) -> bool:
    return await write_status(session_maker, document_id, DocumentStatus.failed(reason))


def _fail_with_session(session, document, reason: str) -> None:
    """Mark a document failed on the caller's (start-up) session."""
    from app.tasks.document_processors.base import get_current_timestamp

    document.status = DocumentStatus.failed(reason)
    document.updated_at = get_current_timestamp()


# ---------------------------------------------------------------------------
# Upload hand-off
# ---------------------------------------------------------------------------


async def start_conversion(
    session,
    *,
    document_id: int,
    temp_path: str,
    filename: str,
    workspace_id: int,
    user_id: str,
    use_vision_llm: bool,
    processing_mode: str,
    store: JobStore,
    client: MinerUClient,
    enqueue: Callable[[int, float], None],
) -> None:
    """Count pages, persist the job, and enqueue the first batch."""
    source = Path(temp_path)
    document = None
    try:
        document = await session.get(Document, document_id)
        if document is None:
            return

        try:
            previous = store.load(document_id)
        except CorruptJobError:
            # Unreadable leftover state: the new upload simply replaces it.
            logger.warning(
                "discarding corrupt conversion state for document %s", document_id
            )
            previous = None

        async def discard_previous() -> None:
            """Stop an earlier conversion of this document (any early failure)."""
            if previous is not None and previous.mineru_task_id:
                await client.cancel(previous.mineru_task_id)
            store.delete(document_id)

        suffix = _suffix(filename)
        pages_total: int | None = None
        if suffix == ".pdf":
            try:
                pages_total = _pdf_page_count(source)
            except Exception as exc:
                # pypdf raises an undocumented range on malformed files: stay a
                # boundary, keep the fixed reason below, log the traceback.
                logger.warning(
                    "cannot read PDF page count for %s", document_id, exc_info=True
                )
                _fail_with_session(session, document, f"Cannot read PDF: {exc}")
                await session.commit()
                await discard_previous()
                return
            if pages_total == 0:
                _fail_with_session(session, document, "PDF has no pages")
                await session.commit()
                await discard_previous()
                return

        job = Job(
            document_id=document_id,
            filename=filename,
            workspace_id=workspace_id,
            user_id=user_id,
            use_vision_llm=use_vision_llm,
            processing_mode=processing_mode,
            source_name=source_name_for(filename),
            source_sha256=_sha256(source),
            source_size=source.stat().st_size,
            pages_total=pages_total,
            batch_pages=int(config.MINERU_BATCH_PAGES),
        )

        try:
            store.create(job, source)
        except ConversionCapacityError:
            _fail_with_session(session, document, CAPACITY_REASON)
            await session.commit()
            await discard_previous()
            return

        # A re-upload while the old conversion is still running replaces the job;
        # the old MinerU task is cancelled best effort so two loops never run.
        if previous is not None and previous.mineru_task_id:
            await client.cancel(previous.mineru_task_id)

        if suffix in _HEIC_SUFFIXES:
            # MinerU cannot read HEIC/HEIF: keep a PNG next to the job state.
            original = store.source_path(job)
            png = original.with_name("source.png")
            try:
                heic_to_png(original, png)
            except (ImportError, ValueError) as exc:
                # heic_to_png turns every decoder failure into ValueError; only
                # its imports raise anything else. A real bug reaches the outer
                # handler, which logs it and fails the document generically.
                _fail_with_session(session, document, str(exc))
                await session.commit()
                await discard_previous()
                return
            _unlink(original)
            job.source_name = "source.png"
            # The bytes MinerU will see are the PNG, not the HEIC upload.
            job.source_sha256 = _sha256(png)
            job.source_size = png.stat().st_size
            store.save(job)

        document.status = progress_status(job, DocumentStatus.STAGE_QUEUED)
        from app.tasks.document_processors.base import get_current_timestamp

        document.updated_at = get_current_timestamp()
        await session.commit()

        enqueue(document_id, 0)
    except Exception:
        # Nothing above may leave a document stuck `pending` forever: a pending
        # row cannot be deleted through the API and no sweep owns it.
        logger.exception("could not start conversion for document %s", document_id)
        if document is not None:
            try:
                # The failure may have been a DB error; without a rollback the
                # commit below raises PendingRollbackError and the row stays pending.
                await session.rollback()
                document = await session.get(Document, document_id) or document
                _fail_with_session(session, document, START_ERROR_REASON)
                await session.commit()
            except Exception:  # e.g. a stale pooled connection; still clean up below
                logger.exception(
                    "could not mark document %s failed after a start error", document_id
                )
        try:
            store.delete_staging(document_id)
        except Exception:
            logger.warning(
                "could not remove conversion staging for document %s",
                document_id,
                exc_info=True,
            )
        try:
            store.delete(document_id)
        except Exception:
            logger.warning(
                "could not remove conversion job for document %s",
                document_id,
                exc_info=True,
            )
    finally:
        # The upload temp file is always ours to clean up, on every path.
        _unlink(source)


# ---------------------------------------------------------------------------
# One batch per step
# ---------------------------------------------------------------------------


async def run_step(
    document_id: int,
    *,
    store: JobStore,
    client: MinerUClient | None = None,
    session_maker: Callable[[], Any],
    sleep: Callable[[float], Any] = asyncio.sleep,
    clock: Callable[[], float] = time.time,
) -> tuple[StepOutcome, float]:
    """Advance *document_id* by at most one MinerU batch."""
    try:
        # Client construction and state loading happen inside the counted
        # path too: a bad MINERU_* setting or an unreadable disk must not
        # crash-loop the worker ([fix 3]).
        if client is None:
            client = client_from_config()
        job = store.load(document_id)
    except CorruptJobError:
        return await _handle_corrupt(
            document_id, store=store, session_maker=session_maker
        )
    except JobGone:
        return StepOutcome.STOPPED, 0.0
    except (OSError, ValueError):
        # The body builds the client (a malformed MINERU_URL raises ValueError
        # from the origin check) and reads job state off disk (OSError).
        return await _handle_setup_error(
            document_id, store=store, session_maker=session_maker
        )
    document = await read_document(session_maker, document_id)

    if document is None:
        if job is not None and job.mineru_task_id:
            await client.cancel(job.mineru_task_id)
        store.delete(document_id)
        return StepOutcome.STOPPED, 0.0

    state = DocumentStatus.get_state(document.status)
    if job is None or state != DocumentStatus.PROCESSING:
        if state in (DocumentStatus.READY, DocumentStatus.FAILED):
            store.delete(document_id)
        return StepOutcome.STOPPED, 0.0

    ranges = job.batch_ranges()
    if job.next_batch < len(ranges):
        try:
            early = await _convert_one_batch(
                job,
                ranges[job.next_batch],
                store=store,
                client=client,
                session_maker=session_maker,
                sleep=sleep,
                clock=clock,
            )
            if early is not None:
                return early, 0.0
        except JobGone:
            # The document (and its directory) went away mid-step: stop
            # quietly, exactly like "document deleted" ([T5 I2]).
            return StepOutcome.STOPPED, 0.0
        except MinerUUnavailable as exc:
            if _superseded(job, store):
                return _superseded_outcome(job, store), 0.0
            return await _handle_unavailable(
                job,
                exc,
                store=store,
                session_maker=session_maker,
                clock=clock,
            )
        except MinerUTaskFailed as exc:
            if _superseded(job, store):
                return _superseded_outcome(job, store), 0.0
            await _fail(session_maker, document_id, f"MinerU: {exc.message}")
            store.delete(document_id)
            return StepOutcome.STOPPED, 0.0
        except MinerUError as exc:
            if _superseded(job, store):
                return _superseded_outcome(job, store), 0.0
            await _fail(session_maker, document_id, f"MinerU: {exc.message}")
            store.delete(document_id)
            return StepOutcome.STOPPED, 0.0
        except Exception:
            return await _handle_step_error(
                job, store=store, session_maker=session_maker
            )
        return StepOutcome.CONTINUE, 0.0

    return await _finish_conversion(job, document_id, store, session_maker)


def _superseded(job: Job, store: JobStore) -> bool:
    """True when this job is gone or was replaced while a step was in flight.

    The step must then do nothing at all: no batch write, no failure status and
    no ``store.save`` that would clobber the replacement's ``job.json``.
    """
    try:
        current = store.load(job.document_id)
    except CorruptJobError:
        # Mid-step corruption can only come from our own writes; do not let a
        # guard against replacement turn into a crash.
        return False
    return current is None or current.created_at != job.created_at


def _replaced(job: Job, store: JobStore) -> bool:
    """True when another (live) job took over this document."""
    try:
        current = store.load(job.document_id)
    except CorruptJobError:
        return False
    return current is not None and current.created_at != job.created_at


def _superseded_outcome(job: Job, store: JobStore) -> StepOutcome:
    """A live replacement still needs a step, so keep its chain going."""
    return StepOutcome.CONTINUE if _replaced(job, store) else StepOutcome.STOPPED


def _backoff_seconds(failures: int) -> float:
    return min(60.0 * (2 ** max(0, failures - 1)), MAX_BACKOFF_SECONDS)


def _batch_expired(job: Job, clock: Callable[[], float]) -> bool:
    """True when the current batch's MinerU task ran past its deadline."""
    if job.batch_submitted_at is None:
        return False
    return clock() - job.batch_submitted_at > float(config.MINERU_BATCH_TIMEOUT_SECONDS)


def _clear_unavailable(job: Job) -> bool:
    """Reset the 24 h clock after a successful submit/poll (M3)."""
    if job.unreachable_since is None and job.attempts == 0:
        return False
    job.unreachable_since = None
    job.attempts = 0
    return True


async def _convert_one_batch(
    job: Job,
    page_range: str | None,
    *,
    store: JobStore,
    client: MinerUClient,
    session_maker: Callable[[], Any],
    sleep: Callable[[float], Any],
    clock: Callable[[], float],
) -> StepOutcome | None:
    """Submit (or resume) one MinerU task and poll it to completion.

    Returns ``None`` when a batch finished, or a terminal-ish outcome when the
    job was superseded mid-flight (CONTINUE keeps a live replacement moving).
    """
    not_found = 0
    while True:
        if _superseded(job, store):
            return _superseded_outcome(job, store)
        if job.mineru_task_id is None:
            task_id = await client.submit(
                store.source_path(job),
                job.source_name,
                page_range=page_range,
                size=job.source_size,
                sha256=job.source_sha256,
            )
            if _superseded(job, store):
                return _superseded_outcome(job, store)
            # Persist the id *before* polling: a crash resumes this task. The
            # submit time is persisted too, so the batch deadline survives a
            # worker restart.
            job.mineru_task_id = task_id
            job.batch_submitted_at = clock()
            _clear_unavailable(job)
            store.save(job)
        elif job.batch_submitted_at is None:
            # A job file written before the deadline existed: start its clock.
            job.batch_submitted_at = clock()
            store.save(job)

        if _superseded(job, store):
            return _superseded_outcome(job, store)
        await write_status(
            session_maker,
            job.document_id,
            progress_status(job, DocumentStatus.STAGE_CONVERTING),
        )
        last_touch = clock()
        while True:
            if _superseded(job, store):
                # A re-upload replaced this job while we were polling; stop
                # before touching the replacement's state (M1).
                return _superseded_outcome(job, store)
            try:
                state = await client.get(job.mineru_task_id)
            except MinerUTaskNotFound:
                # mineru-api restarted and lost its uploads: submit again.
                not_found += 1
                if not_found > 1:
                    raise MinerUTaskFailed(
                        "task_not_found",
                        "the task vanished again after resubmission",
                    ) from None
                if _superseded(job, store):
                    return _superseded_outcome(job, store)
                job.mineru_task_id = None
                store.save(job)
                break

            # A successful poll means MinerU is back: restart the 24 h clock so
            # a later blip cannot trip it early (M3).
            if _clear_unavailable(job) and not _superseded(job, store):
                store.save(job)
            if _superseded(job, store):
                return _superseded_outcome(job, store)

            if state.status in ("completed", "partial"):
                if not state.result_url:
                    raise MinerUError(
                        "invalid_response",
                        f"MinerU task {job.mineru_task_id} finished without a result",
                    )
                markdown = await client.fetch_markdown(state.result_url)
                if _superseded(job, store):
                    return _superseded_outcome(job, store)
                store.write_batch(job, job.next_batch, markdown)
                job.next_batch += 1
                job.mineru_task_id = None
                job.batch_submitted_at = None
                job.batch_resubmits = 0
                job.unreachable_since = None
                job.attempts = 0
                job.step_errors = 0
                store.save(job)
                await write_status(
                    session_maker,
                    job.document_id,
                    progress_status(job, DocumentStatus.STAGE_CONVERTING),
                )
                return None

            if _batch_expired(job, clock):
                # A normal batch takes ~4 min; 30 min with no result means the
                # task is hung. Cancel it and re-submit the same range once.
                if job.batch_resubmits >= 1:
                    logger.warning(
                        "MinerU task for document %s timed out twice; failing",
                        job.document_id,
                    )
                    await client.cancel(job.mineru_task_id)
                    await _fail(session_maker, job.document_id, BATCH_TIMEOUT_REASON)
                    store.delete(job.document_id)
                    return StepOutcome.STOPPED
                logger.warning(
                    "MinerU task for document %s exceeded %ss; cancelling and "
                    "resubmitting batch",
                    job.document_id,
                    config.MINERU_BATCH_TIMEOUT_SECONDS,
                )
                await client.cancel(job.mineru_task_id)
                job.mineru_task_id = None
                job.batch_submitted_at = None
                job.batch_resubmits += 1
                store.save(job)
                break

            last_touch = await _maybe_touch(
                session_maker, job.document_id, last_touch, clock
            )
            await sleep(float(config.MINERU_POLL_SECONDS))
        # MinerUTaskNotFound: loop round and submit the same range again.


async def _handle_corrupt(
    document_id: int,
    *,
    store: JobStore,
    session_maker: Callable[[], Any],
) -> tuple[StepOutcome, float]:
    """Fail a corrupt job, but only if the document is still processing.

    Never crash-loop a worker on unreadable state ([T5 I1]); also never let a
    stale step overwrite a document that already finished or failed ([fix 3]).
    """
    logger.warning(
        "corrupt conversion state for document %s", document_id, exc_info=True
    )
    document = await read_document(session_maker, document_id)
    if (
        document is not None
        and DocumentStatus.get_state(document.status) == DocumentStatus.PROCESSING
    ):
        await _fail(session_maker, document_id, CORRUPT_REASON)
    store.delete(document_id)
    return StepOutcome.STOPPED, 0.0


async def _handle_setup_error(
    document_id: int,
    *,
    store: JobStore,
    session_maker: Callable[[], Any],
) -> tuple[StepOutcome, float]:
    """Count a failed setup (client, state read) in the step-error budget.

    Called from ``run_step``'s narrowed ``except (OSError, ValueError)`` block, so
    ``_handle_step_error`` still logs this exception with its traceback.
    """
    logger.exception("conversion step setup failed for document %s", document_id)
    try:
        job = store.load(document_id)
    except CorruptJobError:
        return await _handle_corrupt(
            document_id, store=store, session_maker=session_maker
        )
    except OSError:
        # Even the retry read failed; no state to count against, so back off
        # and let a later step (or the sweep) try again.
        logger.warning(
            "could not read conversion state for document %s",
            document_id,
            exc_info=True,
        )
        return StepOutcome.RETRY_LATER, MAX_BACKOFF_SECONDS
    if job is None:
        return StepOutcome.STOPPED, 0.0
    return await _handle_step_error(job, store=store, session_maker=session_maker)


async def _handle_step_error(
    job: Job,
    *,
    store: JobStore,
    session_maker: Callable[[], Any],
) -> tuple[StepOutcome, float]:
    """Count an unexpected step failure, back off, and fail after N (M4).

    The stored reason is a fixed sentence: exception text can carry file paths
    or credentials, so it only goes to the log.
    """
    logger.exception(
        "conversion step for document %s failed unexpectedly", job.document_id
    )
    if _superseded(job, store):
        return _superseded_outcome(job, store), 0.0

    job.step_errors += 1
    try:
        store.save(job)
    except JobGone:
        return StepOutcome.STOPPED, 0.0
    except OSError:
        logger.warning(
            "could not persist the conversion error count for document %s",
            job.document_id,
            exc_info=True,
        )
        return StepOutcome.RETRY_LATER, MAX_BACKOFF_SECONDS

    if job.step_errors >= MAX_STEP_ERRORS:
        await _fail(session_maker, job.document_id, STEP_ERROR_REASON)
        store.delete(job.document_id)
        return StepOutcome.STOPPED, 0.0

    try:
        await write_status(
            session_maker,
            job.document_id,
            progress_status(job, DocumentStatus.STAGE_CONVERTING),
        )
    except DB_FAULTS:  # the database may be the reason the step failed
        logger.warning(
            "could not refresh conversion progress for document %s",
            job.document_id,
            exc_info=True,
        )
    return StepOutcome.RETRY_LATER, _backoff_seconds(job.step_errors)


async def _maybe_touch(
    session_maker: Callable[[], Any],
    document_id: int,
    last_touch: float,
    clock: Callable[[], float],
) -> float:
    """Refresh ``status.updated_at`` at most once a minute while polling."""
    if clock() - last_touch < TOUCH_SECONDS:
        return last_touch
    try:
        async with session_maker()() as session:
            await touch_status(session, document_id)
    except DB_FAULTS:  # a lost heartbeat must not abort the conversion
        logger.warning(
            "conversion keep-alive for document %s failed", document_id, exc_info=True
        )
    return clock()


async def _handle_unavailable(
    job: Job,
    exc: MinerUUnavailable,
    *,
    store: JobStore,
    session_maker: Callable[[], Any],
    clock: Callable[[], float],
) -> tuple[StepOutcome, float]:
    """Back off while MinerU is unreachable; give up after 24 h."""
    if job.unreachable_since is None:
        job.unreachable_since = clock()
    job.attempts += 1
    try:
        store.save(job)
    except JobGone:
        return StepOutcome.STOPPED, 0.0
    await write_status(
        session_maker,
        job.document_id,
        progress_status(job, DocumentStatus.STAGE_WAITING),
    )

    if clock() - job.unreachable_since > float(config.MINERU_GIVE_UP_SECONDS):
        await _fail(session_maker, job.document_id, f"{GIVE_UP_REASON}: {exc.message}")
        store.delete(job.document_id)
        return StepOutcome.STOPPED, 0.0

    return StepOutcome.RETRY_LATER, _backoff_seconds(job.attempts)


def _write_handoff(markdown: str, filename: str) -> Path:
    """Write the merged Markdown to a hand-off file.

    ``tempfile`` honours ``TMPDIR``; the upload route uses the same mechanism
    (in the image both point at the ``/shared_tmp`` volume), so the hand-off
    behaves exactly like a normal upload's temp file.
    """
    suffix = PurePosixPath(filename).suffix
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=suffix, prefix=HANDOFF_PREFIX
    ) as tmp:
        tmp.write(markdown.encode("utf-8"))
        tmp.flush()
        os.fsync(tmp.fileno())
        return Path(tmp.name)


def _delete_if_current(job: Job, store: JobStore) -> None:
    """Drop the job directory only if it is still the job this step owns.

    Indexing can outlive a re-upload; deleting then would destroy the
    replacement's in-flight state. Unreadable state is left to the sweep.
    """
    try:
        current = store.load(job.document_id)
    except CorruptJobError:
        return
    if current is None or current.created_at != job.created_at:
        return
    store.delete(job.document_id)


async def _finish_url_conversion(
    job: Job,
    document: Document,
    merged: str,
    *,
    store: JobStore,
    session_maker: Callable[[], Any],
) -> tuple[StepOutcome, float]:
    """Index a saved URL's PDF output without changing the row's identity."""
    from app.conversion.status import status_keepalive
    from app.indexing_pipeline.adapters.url_adapter import UrlDocumentAdapter

    url = (document.document_metadata or {}).get("url")
    if not isinstance(url, str) or not url:
        await _fail(session_maker, job.document_id, "URL document has no source URL")
        _delete_if_current(job, store)
        return StepOutcome.STOPPED, 0.0

    try:
        async with (
            session_maker()() as session,
            status_keepalive(job.document_id, session_maker=session_maker),
        ):
            await UrlDocumentAdapter(session).index(
                merged,
                document.title or url,
                url,
                document_type=DocumentType.CRAWLED_URL,
                workspace_id=job.workspace_id,
                user_id=job.user_id,
                folder_id=document.folder_id,
            )
    except Exception:
        # The indexing pipeline usually persisted a specific failure itself;
        # only fill in a generic reason when it did not.
        logger.warning(
            "Indexing URL document %s failed", job.document_id, exc_info=True
        )
        current = await read_document(session_maker, job.document_id)
        if current is not None and not DocumentStatus.is_state(
            current.status, DocumentStatus.FAILED
        ):
            await _fail(session_maker, job.document_id, "Indexing saved URL failed")
        _delete_if_current(job, store)
        return StepOutcome.STOPPED, 0.0

    _delete_if_current(job, store)
    return StepOutcome.DONE, 0.0


async def _finish_conversion(
    job: Job,
    document_id: int,
    store: JobStore,
    session_maker: Callable[[], Any],
) -> tuple[StepOutcome, float]:
    """Merge the batches and hand the Markdown to the indexing pipeline."""
    from app.tasks.celery_tasks.document_tasks import _process_file_with_document

    if _superseded(job, store):
        return _superseded_outcome(job, store), 0.0

    try:
        # All batches must be present and contiguous before the final merge.
        merged = store.merged_markdown(job, expect_complete=True)
    except CorruptJobError:
        logger.warning(
            "incomplete conversion state for document %s", document_id, exc_info=True
        )
        await _fail(session_maker, document_id, CORRUPT_REASON)
        store.delete(document_id)
        return StepOutcome.STOPPED, 0.0
    if not merged.strip():
        await _fail(session_maker, document_id, EMPTY_REASON)
        store.delete(document_id)
        return StepOutcome.STOPPED, 0.0

    # A PDF saved from a URL is a CRAWLED_URL document. The file-upload
    # hand-off would re-identify it as a FILE and overwrite the metadata that
    # citations read, so it goes through the URL adapter instead: same
    # indexing pipeline, same row, URL identity and metadata intact.
    document = await read_document(session_maker, document_id)
    if getattr(document, "document_type", None) == DocumentType.CRAWLED_URL:
        return await _finish_url_conversion(
            job, document, merged, store=store, session_maker=session_maker
        )

    handoff = _write_handoff(merged, job.filename)
    try:
        # A markdown_override skips extraction and credits; the document then
        # goes through chunking/embedding exactly as a normal upload does.
        await _process_file_with_document(
            document_id,
            str(handoff),
            job.filename,
            job.workspace_id,
            job.user_id,
            use_vision_llm=job.use_vision_llm,
            processing_mode=job.processing_mode,
            markdown_override=merged,
        )
    except Exception:
        # _process_file_with_document has already marked the document failed.
        logger.warning(
            "Indexing MinerU output for document %s failed", document_id, exc_info=True
        )
        _delete_if_current(job, store)
        return StepOutcome.STOPPED, 0.0
    finally:
        _unlink(handoff)

    _delete_if_current(job, store)
    return StepOutcome.DONE, 0.0
