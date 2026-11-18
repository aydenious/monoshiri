"""Integration tests: Gmail indexer builds ConnectorDocuments that flow through the pipeline."""

import pytest
from sqlalchemy import select

from app.config import config as app_config
from app.db import Document, DocumentStatus, DocumentType
from app.indexing_pipeline.connector_document import ConnectorDocument
from app.indexing_pipeline.indexing_pipeline_service import IndexingPipelineService

_EMBEDDING_DIM = app_config.embedding_model_instance.dimension

pytestmark = pytest.mark.integration


def _gmail_doc(
    *, unique_id: str, workspace_id: int, connector_id: int, user_id: str
) -> ConnectorDocument:
    """Build a Gmail-style ConnectorDocument like the real indexer does."""
    return ConnectorDocument(
        title=f"Subject for {unique_id}",
        source_markdown=f"## Email\n\nBody of {unique_id}",
        unique_id=unique_id,
        document_type=DocumentType.GOOGLE_GMAIL_CONNECTOR,
        workspace_id=workspace_id,
        connector_id=connector_id,
        created_by_id=user_id,
        metadata={
            "message_id": unique_id,
            "from": "sender@example.com",
            "document_type": "Gmail Message",
        },
    )


@pytest.mark.usefixtures("patched_embed_texts", "patched_chunk_text")
async def test_gmail_pipeline_creates_ready_document(
    db_session, db_workspace, db_connector, db_user, mocker
):
    """A Gmail ConnectorDocument flows through prepare + index to a READY document."""
    space_id = db_workspace.id
    doc = _gmail_doc(
        unique_id="msg-pipeline-1",
        workspace_id=space_id,
        connector_id=db_connector.id,
        user_id=str(db_user.id),
    )

    service = IndexingPipelineService(session=db_session)
    prepared = await service.prepare_for_indexing([doc])
    assert len(prepared) == 1

    await service.index(prepared[0], doc)

    result = await db_session.execute(
        select(Document).filter(Document.workspace_id == space_id)
    )
    row = result.scalars().first()

    assert row is not None
    assert row.document_type == DocumentType.GOOGLE_GMAIL_CONNECTOR
    assert DocumentStatus.is_state(row.status, DocumentStatus.READY)
    assert row.source_markdown == doc.source_markdown
