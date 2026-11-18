"""Rows written by removed connectors must keep listing after the Task 7 strip."""

from __future__ import annotations

import os

import pytest

from app.db import Document, DocumentType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL (DB=1 backend-gate mode)",
    ),
]


async def test_legacy_connector_document_still_listed(
    client, db_session, db_user, db_workspace, auth
):
    doc = Document(
        title="old slack thread",
        document_type=DocumentType.SLACK_CONNECTOR,
        content="legacy content",
        content_hash="legacy-slack-hash",
        unique_identifier_hash="legacy-slack-uid",
        workspace_id=db_workspace.id,
        created_by_id=db_user.id,
        connector_id=None,
        document_metadata={},
    )
    db_session.add(doc)
    await db_session.flush()

    resp = await client.get(
        "/api/v1/documents",
        params={"workspace_id": db_workspace.id},
        headers=await auth(db_user),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(d["id"] == doc.id for d in body["items"])
