from datetime import UTC, datetime
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.conversion.status import STALLED_AFTER, WAITING_STALLED_AFTER
from app.db import DocumentStatus, DocumentType

from .chunks import ChunkRead

T = TypeVar("T")


class ExtensionDocumentMetadata(BaseModel):
    BrowsingSessionId: str
    VisitedWebPageURL: str
    VisitedWebPageTitle: str
    VisitedWebPageDateWithTimeInISOString: str
    VisitedWebPageReffererURL: str
    VisitedWebPageVisitDurationInMilliseconds: str


class ExtensionDocumentContent(BaseModel):
    metadata: ExtensionDocumentMetadata
    pageContent: str  # noqa: N815


class DocumentBase(BaseModel):
    document_type: DocumentType
    content: (
        list[ExtensionDocumentContent] | list[str] | str
    )  # Updated to allow string content
    workspace_id: int


class DocumentsCreate(DocumentBase):
    pass


DocumentHealth = Literal["working", "waiting", "stalled"]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str) and value:
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


class DocumentStatusSchema(BaseModel):
    """Document processing status, including conversion progress and health."""

    state: str  # "pending", "processing", "ready", "failed"
    reason: str | None = None
    stage: str | None = None  # queued|converting|waiting_for_converter|indexing
    pages_done: int | None = None
    pages_total: int | None = None
    updated_at: datetime | None = None
    health: DocumentHealth | None = None

    @classmethod
    def from_document(cls, document, now: datetime) -> "DocumentStatusSchema | None":
        """Build the REST view of a document's status.

        ``health`` is computed, never stored: ``waiting`` while MinerU is
        unreachable, ``stalled`` when nothing refreshed ``updated_at`` for
        :data:`STALLED_AFTER`, ``working`` otherwise. Ready/failed documents
        have no health.
        """
        status = getattr(document, "status", None)
        if not status or not isinstance(status, dict):
            return None

        state = status.get("state") or DocumentStatus.READY
        updated_at = _parse_timestamp(status.get("updated_at")) or _as_utc(
            getattr(document, "updated_at", None)
        )

        health: DocumentHealth | None
        if state in (DocumentStatus.READY, DocumentStatus.FAILED):
            health = None
        elif status.get("stage") == DocumentStatus.STAGE_WAITING:
            # A live wait refreshes updated_at on every retry; one that has not
            # moved for the longest outage backoff is stalled, not waiting.
            if (
                updated_at is not None
                and _as_utc(now) - updated_at > WAITING_STALLED_AFTER
            ):
                health = "stalled"
            else:
                health = "waiting"
        elif updated_at is not None and _as_utc(now) - updated_at > STALLED_AFTER:
            health = "stalled"
        else:
            health = "working"

        return cls(
            state=state,
            reason=status.get("reason"),
            stage=status.get("stage"),
            pages_done=status.get("pages_done"),
            pages_total=status.get("pages_total"),
            updated_at=updated_at,
            health=health,
        )


class FileUploadResponse(BaseModel):
    """Body of ``POST /api/v1/documents/fileupload``.

    ``document_ids`` are the rows created or re-queued for processing,
    ``duplicate_document_ids`` the ready rows it skipped.
    """

    message: str
    document_ids: list[int]
    duplicate_document_ids: list[int]
    total_files: int
    pending_files: int
    skipped_duplicates: int


class DocumentRead(BaseModel):
    id: int
    title: str
    document_type: DocumentType
    document_metadata: dict
    content: str = ""
    content_preview: str = ""
    content_hash: str
    unique_identifier_hash: str | None
    created_at: datetime
    updated_at: datetime | None
    workspace_id: int
    folder_id: int | None = None
    created_by_id: UUID | None = None
    created_by_name: str | None = None
    created_by_email: str | None = None
    status: DocumentStatusSchema | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentWithChunksRead(DocumentRead):
    chunks: list[ChunkRead] = []
    total_chunks: int = 0
    chunk_start_index: int = 0

    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool


class DocumentTitleRead(BaseModel):
    """Lightweight document response for mention picker - only essential fields."""

    id: int
    title: str
    document_type: DocumentType
    folder_id: int | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentTitleSearchResponse(BaseModel):
    """Response for document title search - optimized for typeahead."""

    items: list[DocumentTitleRead]
    has_more: bool


class DocumentStatusItemRead(BaseModel):
    """Lightweight document status payload for batch status polling."""

    id: int
    title: str
    document_type: DocumentType
    status: DocumentStatusSchema

    model_config = ConfigDict(from_attributes=True)


class DocumentStatusBatchResponse(BaseModel):
    """Batch status response for a set of document IDs."""

    items: list[DocumentStatusItemRead]
