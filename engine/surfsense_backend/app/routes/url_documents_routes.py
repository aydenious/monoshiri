"""``POST /api/v1/documents/url`` — save a page, YouTube video or PDF link.

The route owns identity and the pending row: permission, an SSRF check and the
duplicate lookup all happen synchronously, before anything is fetched or
created. Classification is cheap (no request); the heavy work is handed to
``ingest_url_document``.

A save is one-shot (spec settled point 7): an existing READY document for the
same URL is returned as-is, with no second fetch. A row that is still
pending/processing is likewise returned without dispatch, so one document can
never run two ingest loops; only a failed row is retried.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.context import AuthContext
from app.conversion.job_store import job_store_from_config
from app.db import (
    Document,
    DocumentStatus,
    DocumentType,
    Folder,
    Permission,
    get_async_session,
)
from app.proprietary.platforms.youtube.url_resolver import get_youtube_video_id
from app.schemas.documents import DocumentStatusSchema
from app.schemas.url_documents import (
    UrlDocumentCreate,
    UrlDocumentCreated,
    UrlRejected,
)
from app.services.url_ingest import canonicalize_url, classify_url
from app.tasks.document_processors.base import get_current_timestamp
from app.users import get_auth_context
from app.utils.crawl.net_guard import is_publicly_routable
from app.utils.document_converters import generate_unique_identifier_hash
from app.utils.rbac import check_permission

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_stalled(document: Document) -> bool:
    """True when an in-flight row has shown no progress for STALLED_AFTER."""
    view = DocumentStatusSchema.from_document(document, datetime.now(UTC))
    return view is not None and view.health == "stalled"


_PERMISSION_MESSAGE = "You don't have permission to create documents in this workspace"
_UNREACHABLE_DETAIL = "URL is not publicly reachable"
_SAVE_FAILED_DETAIL = "Could not save the URL"
_INVALID_URL_DETAIL = "Invalid URL"
_SCHEME_DETAIL = "Only http and https URLs can be saved"
_FOLDER_NOT_FOUND_DETAIL = "Folder not found"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_UNIQUE_HASH_CONSTRAINT = "unique_identifier_hash"
# The longest URL accepted (the old ``HttpUrl`` cap; browsers agree on it).
_MAX_URL_LENGTH = 2083


def _refusal_detail(url: str) -> str | None:
    """The fixed 400 string for a URL the route will not accept, if any.

    A URL is refused before it is stored or fetched: malformed, a scheme other
    than http(s), or a host that is not publicly reachable. The detail is a
    fixed string, never the parser's or the resolver's own message.
    """
    if len(url) > _MAX_URL_LENGTH:
        return _INVALID_URL_DETAIL
    try:
        parsed = urlsplit(url)
        parsed.port  # noqa: B018 - a malformed port raises here
    except ValueError:
        return _INVALID_URL_DETAIL
    scheme = parsed.scheme.lower()
    if not scheme:
        return _INVALID_URL_DETAIL
    if scheme not in _ALLOWED_SCHEMES:
        return _SCHEME_DETAIL
    if not parsed.hostname:
        return _INVALID_URL_DETAIL
    return None


def _is_unique_hash_violation(exc: IntegrityError) -> bool:
    """True only for a unique-identifier-hash clash (the concurrent-POST race).

    Any other integrity error (e.g. a bad ``folder_id`` foreign key) is not a
    race and must surface as the fixed 500, not as a misleading 200.
    """
    orig = getattr(exc, "orig", None)
    constraint = getattr(orig, "constraint_name", None) or getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )
    if isinstance(constraint, str):
        return _UNIQUE_HASH_CONSTRAINT in constraint
    text = str(orig or exc).lower()
    return _UNIQUE_HASH_CONSTRAINT in text and ("unique" in text or "duplicate" in text)


class UrlIngestDispatcher(Protocol):
    async def dispatch_url_ingest(
        self,
        *,
        document_id: int,
        url: str,
        kind: str,
        workspace_id: int,
        user_id: str,
    ) -> None: ...


class CeleryUrlIngestDispatcher:
    """Production dispatcher — fires the Celery task via the Redis broker."""

    async def dispatch_url_ingest(
        self,
        *,
        document_id: int,
        url: str,
        kind: str,
        workspace_id: int,
        user_id: str,
    ) -> None:
        from app.tasks.celery_tasks.url_tasks import ingest_url_document_task

        ingest_url_document_task.delay(
            document_id=document_id,
            url=url,
            kind=kind,
            workspace_id=workspace_id,
            user_id=user_id,
        )


async def get_url_ingest_dispatcher() -> UrlIngestDispatcher:
    return CeleryUrlIngestDispatcher()


@router.post(
    "/documents/url",
    response_model=UrlDocumentCreated,
    status_code=202,
    # 200 = this URL already has a row (ready, or still in flight); generated
    # clients must see it as a documented success, not an unknown status.
    responses={
        200: {
            "model": UrlDocumentCreated,
            "description": "The URL is already saved or being saved",
        },
        # A refused URL is a 400 with a fixed string detail, declared so a
        # generated client decodes an object and not the 422 validation list.
        400: {
            "model": UrlRejected,
            "description": "The URL cannot be saved (invalid, scheme, or host)",
        },
    },
)
async def create_url_document(
    request: UrlDocumentCreate,
    response: Response,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
    dispatcher: UrlIngestDispatcher = Depends(get_url_ingest_dispatcher),
):
    await check_permission(
        session,
        auth,
        request.workspace_id,
        Permission.DOCUMENTS_CREATE.value,
        _PERMISSION_MESSAGE,
    )

    folder_id = request.folder_id
    if folder_id is not None:
        # A folder is a workspace object: a foreign or missing id must not be
        # written onto this workspace's row (it would file the document in a
        # folder the caller may not own). Checked before any write.
        folder = await session.get(Folder, folder_id)
        if folder is None or folder.workspace_id != request.workspace_id:
            raise HTTPException(status_code=404, detail=_FOLDER_NOT_FOUND_DETAIL)

    detail = _refusal_detail(request.url)
    if detail is not None:
        raise HTTPException(status_code=400, detail=detail)

    # One identity per resource: the share sheet's variant forms of one URL
    # (``?si=``, utm_*, youtu.be vs watch) become the same hash and row.
    url = canonicalize_url(request.url)

    # The private-address answer is synchronous: no row, no fetch. A redirect
    # found later in the task fails that row with a reason instead.
    # getaddrinfo blocks, so keep it off the event loop.
    if not await asyncio.to_thread(is_publicly_routable, url):
        raise HTTPException(status_code=400, detail=_UNREACHABLE_DETAIL)

    document_type = (
        DocumentType.YOUTUBE_VIDEO
        if get_youtube_video_id(url)
        else DocumentType.CRAWLED_URL
    )
    unique_identifier_hash = generate_unique_identifier_hash(
        document_type, url, request.workspace_id
    )

    result = await session.execute(
        select(Document).filter(
            Document.unique_identifier_hash == unique_identifier_hash
        )
    )
    existing = result.scalars().first()

    # One-shot: a READY document already holds this URL. Nothing is re-fetched.
    if existing is not None and DocumentStatus.is_state(
        existing.status, DocumentStatus.READY
    ):
        response.status_code = 200
        return UrlDocumentCreated(document_id=existing.id)

    # A row that is still in flight keeps its single loop: return the id and
    # dispatch nothing, so the same document is never processed twice. A row
    # with no sign of life for STALLED_AFTER (broker down at dispatch, worker
    # killed mid-task) has no loop left, so it is retried like a failed row.
    if (
        existing is not None
        and (
            DocumentStatus.is_state(existing.status, DocumentStatus.PENDING)
            or DocumentStatus.is_state(existing.status, DocumentStatus.PROCESSING)
        )
        and not _is_stalled(existing)
    ):
        response.status_code = 200
        return UrlDocumentCreated(document_id=existing.id)

    kind = classify_url(url)

    retrying = existing is not None
    if existing is not None:
        # A failed row may be retried. The unique-hash constraint allows
        # exactly one row, so reset it rather than inserting a second one.
        existing.title = url
        existing.content = "Processing..."
        existing.document_metadata = {
            **(existing.document_metadata or {}),
            "url": url,
            "source_url": url,
        }
        existing.status = DocumentStatus.pending()
        existing.updated_at = get_current_timestamp()
        if folder_id is not None:
            existing.folder_id = folder_id
        document = existing
    else:
        document = Document(
            workspace_id=request.workspace_id,
            title=url,
            document_type=document_type,
            document_metadata={"url": url, "source_url": url},
            content="Processing...",
            content_hash=unique_identifier_hash,
            unique_identifier_hash=unique_identifier_hash,
            embedding=None,
            status=DocumentStatus.pending(),
            updated_at=get_current_timestamp(),
            created_by_id=str(auth.user.id),
            folder_id=folder_id,
        )
        session.add(document)

    try:
        await session.commit()
        await session.refresh(document)
    except IntegrityError as exc:
        await session.rollback()
        if not _is_unique_hash_violation(exc):
            # A foreign-key failure (e.g. a bad folder) or any other
            # integrity problem is not the concurrent-POST race.
            logger.exception(
                "IntegrityError saving URL document in workspace %s",
                request.workspace_id,
            )
            raise HTTPException(status_code=500, detail=_SAVE_FAILED_DETAIL) from None
        # Two concurrent POSTs for one URL: the other request won the insert.
        # Return its row (never a 500) and let its own dispatch own the work.
        result = await session.execute(
            select(Document).filter(
                Document.unique_identifier_hash == unique_identifier_hash
            )
        )
        raced = result.scalars().first()
        if raced is None:
            logger.exception(
                "IntegrityError for URL document but no row was found in workspace %s",
                request.workspace_id,
            )
            raise HTTPException(status_code=500, detail=_SAVE_FAILED_DETAIL) from None
        response.status_code = 200
        return UrlDocumentCreated(document_id=raced.id)
    except Exception:
        await session.rollback()
        # Never echo the exception: it can carry SQL or driver internals.
        logger.exception(
            "Failed to save URL document in workspace %s", request.workspace_id
        )
        raise HTTPException(status_code=500, detail=_SAVE_FAILED_DETAIL) from None

    if retrying:
        # Supersede any earlier conversion before the new task starts: its
        # step finds no job directory and stops instead of indexing over the
        # retried row.
        await asyncio.to_thread(job_store_from_config().delete, document.id)

    try:
        await dispatcher.dispatch_url_ingest(
            document_id=document.id,
            url=url,
            kind=kind,
            workspace_id=request.workspace_id,
            user_id=str(auth.user.id),
        )
    except Exception:
        # The row is already committed. Leaving it pending would make an
        # immediate re-POST a no-op for STALLED_AFTER; failing it means the
        # iOS client's retry dispatches straight away.
        logger.exception("Could not dispatch URL ingest for document %s", document.id)
        document.status = DocumentStatus.failed(_SAVE_FAILED_DETAIL)
        document.updated_at = get_current_timestamp()
        try:
            await session.commit()
        except Exception:
            await session.rollback()
        raise HTTPException(status_code=500, detail=_SAVE_FAILED_DETAIL) from None
    return UrlDocumentCreated(document_id=document.id)
