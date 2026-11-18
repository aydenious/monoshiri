"""REST document status carries conversion progress and computed health."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.conversion.status import status_keepalive
from app.db import Document, DocumentStatus, DocumentType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL (DB=1 run-tests.sh mode)",
    ),
]


async def _make_document(
    db_session,
    workspace,
    user,
    *,
    status: dict,
    updated_at: datetime | None = None,
    title: str = "status doc",
) -> Document:
    doc = Document(
        title=title,
        document_type=DocumentType.FILE,
        content="content",
        content_hash=f"hash-{title}",
        workspace_id=workspace.id,
        created_by_id=user.id,
        document_metadata={},
        status=status,
        updated_at=updated_at,
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


def _old_iso(minutes: int = 20) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def test_get_document_includes_status_and_health(
    client, db_session, db_user, db_workspace, auth
):
    doc = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status=DocumentStatus.processing(
            stage="converting", pages_done=20, pages_total=400
        ),
    )

    response = await client.get(
        f"/api/v1/documents/{doc.id}", headers=await auth(db_user)
    )
    assert response.status_code == 200, response.text
    status = response.json()["status"]
    assert status["state"] == "processing"
    assert status["health"] == "working"
    assert status["stage"] == "converting"
    assert status["pages_done"] == 20
    assert status["pages_total"] == 400
    assert status["updated_at"] is not None


async def test_list_and_batch_status_include_progress(
    client, db_session, db_user, db_workspace, auth
):
    doc = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status={
            "state": "processing",
            "stage": "converting",
            "pages_done": 30,
            "pages_total": 400,
            "updated_at": _old_iso(),
        },
        updated_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    headers = await auth(db_user)

    listed = await client.get(
        "/api/v1/documents",
        params={"workspace_id": db_workspace.id},
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    item = next(d for d in listed.json()["items"] if d["id"] == doc.id)
    assert item["status"]["pages_done"] == 30
    assert item["status"]["pages_total"] == 400
    assert item["status"]["health"] == "stalled"

    batch = await client.get(
        "/api/v1/documents/status",
        params={"workspace_id": db_workspace.id, "document_ids": str(doc.id)},
        headers=headers,
    )
    assert batch.status_code == 200, batch.text
    item = batch.json()["items"][0]
    assert item["status"]["state"] == "processing"
    assert item["status"]["stage"] == "converting"
    assert item["status"]["pages_done"] == 30
    assert item["status"]["health"] == "stalled"


async def test_keepalive_refreshes_updated_at(
    client, db_session, db_user, db_workspace
):
    doc = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status={
            "state": "processing",
            "stage": "indexing",
            "updated_at": _old_iso(),
        },
        updated_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    ready = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status={"state": "ready"},
        title="already ready",
    )

    # The keepalive normally opens its own celery session; bind one to the test
    # transaction so it can see the uncommitted rows.
    maker = async_sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with status_keepalive(doc.id, every=0.05, session_maker=lambda: maker):
        await asyncio.sleep(0.25)
    async with status_keepalive(ready.id, every=0.05, session_maker=lambda: maker):
        await asyncio.sleep(0.25)

    await db_session.refresh(doc)
    await db_session.refresh(ready)

    refreshed = _parse(doc.status["updated_at"])
    assert refreshed > datetime.now(UTC) - timedelta(minutes=1)
    assert ready.status == {"state": "ready"}


async def test_batch_status_tolerates_legacy_rows_without_status(
    client, db_session, db_user, db_workspace, auth
):
    """An empty/NULL status must not 500 the whole batch (T4 M1)."""
    empty = await _make_document(
        db_session, db_workspace, db_user, status={}, title="empty status"
    )
    missing = await _make_document(
        db_session, db_workspace, db_user, status=None, title="null status"
    )

    batch = await client.get(
        "/api/v1/documents/status",
        params={
            "workspace_id": db_workspace.id,
            "document_ids": f"{empty.id},{missing.id}",
        },
        headers=await auth(db_user),
    )

    assert batch.status_code == 200, batch.text
    items = {item["id"]: item["status"] for item in batch.json()["items"]}
    assert items[empty.id]["state"] == "ready"
    assert items[missing.id]["state"] == "ready"
    assert items[empty.id]["health"] is None


async def test_indexing_keeps_the_indexing_stage(
    db_session, db_workspace, db_user, make_connector_document
):
    """index() must not wipe stage=indexing while it chunks and embeds (T4 I1)."""
    from app.indexing_pipeline.indexing_pipeline_service import IndexingPipelineService

    connector_doc = make_connector_document(workspace_id=db_workspace.id)
    service = IndexingPipelineService(session=db_session)
    prepared = await service.prepare_for_indexing([connector_doc])
    document = prepared[0]

    seen: dict = {}
    original = service._load_existing_chunks

    async def spy(document_id: int):
        seen["status"] = dict(document.status or {})
        return await original(document_id)

    service._load_existing_chunks = spy
    await service.index(document, connector_doc)

    assert seen["status"]["state"] == "processing"
    assert seen["status"]["stage"] == "indexing"
    assert DocumentStatus.is_state(document.status, DocumentStatus.READY)
