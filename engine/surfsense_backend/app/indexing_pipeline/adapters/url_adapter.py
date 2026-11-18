"""Index a saved URL's readable content under its URL identity.

Modelled on :class:`UploadDocumentAdapter`, but the ConnectorDocument's
``unique_id`` is the URL and its type is ``CRAWLED_URL`` or
``YOUTUBE_VIDEO``. That keeps the document row created by the route — and its
``document_metadata["url"]`` used for citations — in place through indexing.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DocumentStatus, DocumentType
from app.indexing_pipeline.connector_document import ConnectorDocument
from app.indexing_pipeline.indexing_pipeline_service import IndexingPipelineService


class UrlDocumentAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._service = IndexingPipelineService(session)

    async def index(
        self,
        markdown_content: str,
        title: str,
        url: str,
        *,
        document_type: DocumentType,
        workspace_id: int,
        user_id: str,
        folder_id: int | None = None,
    ) -> None:
        connector_doc = ConnectorDocument(
            title=title,
            source_markdown=markdown_content,
            unique_id=url,
            document_type=document_type,
            workspace_id=workspace_id,
            created_by_id=user_id,
            connector_id=None,
            should_use_code_chunker=False,
            metadata={"url": url, "source_url": url},
            folder_id=folder_id,
        )

        documents = await self._service.prepare_for_indexing([connector_doc])
        if not documents:
            raise RuntimeError("prepare_for_indexing returned no documents")

        indexed = await self._service.index_unless_store_owns(
            documents[0], connector_doc
        )

        # A deferral recorded the URL for the knowledge store's indexer; the
        # row exists and stays pending until that build finishes.
        if indexed is None:
            return

        if not DocumentStatus.is_state(indexed.status, DocumentStatus.READY):
            raise RuntimeError(indexed.status.get("reason", "Indexing failed"))

        indexed.content_needs_reindexing = False
        await self._session.commit()
