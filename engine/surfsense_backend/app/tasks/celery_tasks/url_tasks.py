"""Celery task for a one-shot URL save.

The route already created the document row. This task fetches/extracts once and
hands the text to :class:`UrlDocumentAdapter`; a PDF link instead starts the
durable MinerU conversion, whose final hand-off is indexed by the same adapter
(``app.conversion.runner``), so the row keeps its URL identity and metadata.

Any failure marks the row ``failed`` with a readable reason. A save is
one-shot (spec settled point 7): nothing is re-crawled later.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import uuid
from pathlib import Path

from app.celery_app import celery_app
from app.conversion.runner import start_conversion
from app.db import Document, DocumentStatus
from app.indexing_pipeline.adapters.url_adapter import UrlDocumentAdapter
from app.proprietary.platforms.youtube.subtitles import fetch_subtitles
from app.proprietary.platforms.youtube.url_resolver import get_youtube_video_id
from app.services import url_ingest
from app.services.url_ingest import NoReadableTextError
from app.tasks.celery_tasks import get_celery_session_maker, run_async_celery_task
from app.tasks.document_processors.base import get_current_timestamp
from app.utils.crawl.safe_fetch import (
    FetchedPage,
    FetchError,
    UnsafeUrlError,
    is_http_error,
    safe_get,
)

logger = logging.getLogger(__name__)

_PDF_SUFFIX = ".pdf"

# User-visible failure reasons are a curated, fixed vocabulary. Raw exception
# text can carry SQL, driver internals or a stack of library messages, so it is
# only ever logged.
REASON_UNREACHABLE = "URL is not publicly reachable"
REASON_FILE_TOO_LARGE = "File is too large"
REASON_FETCH_FAILED = "Could not fetch the URL"
REASON_NO_TRANSCRIPT = "No transcript available for this video"
REASON_GENERIC = "Could not save the URL"

_TOO_LARGE = "too large"
_PDF_MAGIC = b"%PDF-"

# The transcript API uses blocking ``requests`` with no timeout of its own; a
# hung connection must not hold a fast-queue worker slot forever.
TRANSCRIPT_TIMEOUT_SECONDS = 30.0

# oEmbed is only for the title: a failure must never fail the save.
_OEMBED_TIMEOUT_SECONDS = 5.0


class NoTranscriptError(RuntimeError):
    """The video has no usable transcript (none available or empty)."""


def _shared_tmp_path() -> Path:
    """``/shared_tmp/<uuid>.pdf`` in the image; the local tmp dir in tests."""
    shared = Path("/shared_tmp")
    base = shared if shared.is_dir() else Path(tempfile.gettempdir())
    return base / f"{uuid.uuid4().hex}{_PDF_SUFFIX}"


def _failure_reason(exc: Exception) -> str:
    """Map an exception to the fixed reason shown to the user."""
    if isinstance(exc, UnsafeUrlError):
        return REASON_UNREACHABLE
    if isinstance(exc, FetchError):
        # safe_get's exact marker for a body past max_bytes.
        if str(exc).strip() == _TOO_LARGE:
            return REASON_FILE_TOO_LARGE
        return REASON_FETCH_FAILED
    if is_http_error(exc):
        return REASON_FETCH_FAILED
    if isinstance(exc, NoReadableTextError):
        return str(exc)
    if isinstance(exc, NoTranscriptError):
        return REASON_NO_TRANSCRIPT
    return REASON_GENERIC


async def _write_status(
    session, document_id: int, status: dict, *, title: str | None = None
) -> bool:
    """Write ``documents.status`` unless the row is gone or being deleted.

    Mirrors ``app.conversion.runner.write_status``: a late task write must
    never resurrect a row the delete route is removing.
    """
    await session.rollback()
    document = await session.get(Document, document_id, populate_existing=True)
    if document is None:
        return False
    if DocumentStatus.get_state(document.status) == "deleting":
        return False
    document.status = status
    if title is not None:
        document.title = title
    document.updated_at = get_current_timestamp()
    await session.commit()
    return True


async def _mark_failed(session, document_id: int, reason: str) -> None:
    """Mark the row failed, unless it is being deleted or already failed.

    The indexing pipeline commits its own specific reason (e.g. ``Duplicate
    content: matches '<title>'``) before raising; overwriting that with the
    generic reason would hide the one useful message from the user.
    """
    await session.rollback()
    document = await session.get(Document, document_id, populate_existing=True)
    if document is None:
        return
    state = DocumentStatus.get_state(document.status)
    if state == "deleting" or state == DocumentStatus.FAILED:
        return
    document.status = DocumentStatus.failed(reason)
    document.updated_at = get_current_timestamp()
    await session.commit()


async def _index_markdown(
    session,
    document: Document,
    markdown: str,
    url: str,
    *,
    title: str,
    user_id: str,
) -> None:
    from app.conversion.status import status_keepalive

    # The stage changes only now: the content has been fetched and extracted,
    # so "indexing" is the truth. Setting it before the download mislabelled a
    # slow fetch as indexing. The title is persisted in the same write; a
    # deleted row stops here.
    if not await _write_status(
        session,
        document.id,
        DocumentStatus.processing(stage=DocumentStatus.STAGE_INDEXING),
        title=title,
    ):
        return

    # Indexing can outlive the 15-minute "stalled" window on a large page, so
    # refresh status.updated_at exactly as the file pipeline does.
    async with status_keepalive(document.id):
        await UrlDocumentAdapter(session).index(
            markdown,
            title,
            url,
            document_type=document.document_type,
            workspace_id=document.workspace_id,
            user_id=user_id,
            folder_id=document.folder_id,
        )


async def _transcript_srt(video_id: str) -> str:
    """The SRT body of an available transcript: English, else any language.

    A video whose only captions are Japanese is still a video with captions;
    ``language="any"`` asks the API for whatever track it offers. The whole
    call is bounded: the transcript library's ``requests`` call has no timeout.
    """
    async with asyncio.timeout(TRANSCRIPT_TIMEOUT_SECONDS):
        tracks = await fetch_subtitles(video_id)
        if not tracks or not tracks[0].srt:
            tracks = await fetch_subtitles(video_id, language="any")
        if not tracks or not tracks[0].srt:
            raise NoTranscriptError(video_id)
        return tracks[0].srt


async def _youtube_title(video_id: str, url: str) -> str:
    """The video title from oEmbed, or the URL when it cannot be fetched."""
    oembed_url = url_ingest.youtube_oembed_url(video_id)
    try:
        page = await safe_get(oembed_url, timeout=_OEMBED_TIMEOUT_SECONDS)
        payload = json.loads(page.body)
        title = payload.get("title") if isinstance(payload, dict) else None
    except Exception:
        # A missing title must never fail a save that would otherwise work.
        logger.info("no oEmbed title for video %s", video_id, exc_info=True)
        return url
    if isinstance(title, str) and title.strip():
        return title.strip()
    return url


async def _ingest_youtube(
    session, document: Document, url: str, *, user_id: str
) -> None:
    video_id = get_youtube_video_id(url)
    if not video_id:
        raise ValueError("Not a YouTube video URL")

    try:
        srt = await _transcript_srt(video_id)
    except TimeoutError:
        # Curated, like any other missing transcript: never raw exception text.
        raise NoTranscriptError(video_id) from None

    markdown = url_ingest.srt_to_text(srt)
    if not markdown.strip():
        raise NoTranscriptError(video_id)
    title = await _youtube_title(video_id, url)
    await _index_markdown(
        session, document, markdown, url, title=title, user_id=user_id
    )


async def _ingest_page(
    session,
    document: Document,
    url: str,
    *,
    user_id: str,
    page: FetchedPage | None = None,
) -> None:
    page = page if page is not None else await safe_get(url)
    markdown = url_ingest.extract_readable(page.body, page.final_url)
    # The HTML is already in memory: reading its title costs no fetch.
    title = url_ingest.extract_title(page.body) or url
    await _index_markdown(
        session, document, markdown, url, title=title, user_id=user_id
    )


async def _ingest_pdf(
    session,
    document: Document,
    url: str,
    *,
    user_id: str,
    page: FetchedPage | None = None,
) -> None:
    page = page if page is not None else await safe_get(url)

    # The PDF's title is known from its URL before conversion; persist it now
    # so the runner's final hand-off (``document.title or url``) indexes it.
    # Committed before the temp file exists, so a failed commit leaks nothing.
    document.title = url_ingest.pdf_title_from_url(url) or url
    document.updated_at = get_current_timestamp()
    await session.commit()

    from app.conversion.job_store import job_store_from_config
    from app.conversion.mineru_client import client_from_config
    from app.tasks.celery_tasks.conversion_tasks import convert_document_step

    temp_path = _shared_tmp_path()
    try:
        await asyncio.to_thread(temp_path.write_bytes, page.body)
        await start_conversion(
            session,
            document_id=document.id,
            temp_path=str(temp_path),
            filename=temp_path.name,
            workspace_id=document.workspace_id,
            user_id=user_id,
            use_vision_llm=False,
            processing_mode="basic",
            store=job_store_from_config(),
            client=client_from_config(),
            enqueue=lambda i, d: convert_document_step.apply_async((i,), countdown=d),
        )
    finally:
        # start_conversion unlinks its input on every path it reaches; this
        # covers a failure before that (e.g. building the store/client), so a
        # /shared_tmp PDF is never left behind. Idempotent when already gone.
        await asyncio.to_thread(temp_path.unlink, missing_ok=True)


async def ingest_url_document(
    document_id: int, url: str, kind: str, workspace_id: int, user_id: str
) -> None:
    """Fetch and index one saved URL; failures land in ``documents.status``."""
    async with get_celery_session_maker()() as session:
        document = await session.get(Document, document_id)
        if document is None:
            return

        try:
            if kind in ("pdf", "page_or_pdf"):
                # One fetch, then the bytes themselves decide. A ``.pdf`` path
                # that returns an HTML login page must go to extraction (never
                # to MinerU as a broken PDF), and a PDF served as
                # ``application/octet-stream`` must go to the PDF path.
                page = await safe_get(url)
                if page.body[: len(_PDF_MAGIC)] == _PDF_MAGIC:
                    await _ingest_pdf(
                        session, document, url, user_id=user_id, page=page
                    )
                else:
                    await _ingest_page(
                        session, document, url, user_id=user_id, page=page
                    )
            elif kind == "youtube":
                await _ingest_youtube(session, document, url, user_id=user_id)
            else:
                raise ValueError(f"Unknown URL kind: {kind}")
        except Exception as exc:
            logger.warning(
                "URL save for document %s failed", document_id, exc_info=True
            )
            await _mark_failed(session, document_id, _failure_reason(exc))


@celery_app.task(name="ingest_url_document")
def ingest_url_document_task(
    document_id: int, url: str, kind: str, workspace_id: int, user_id: str
):
    return run_async_celery_task(
        lambda: ingest_url_document(document_id, url, kind, workspace_id, user_id)
    )
