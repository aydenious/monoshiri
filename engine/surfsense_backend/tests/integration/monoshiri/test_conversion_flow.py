"""Upload → durable MinerU conversion job → resume sweep → indexing (real DB).

These tests exercise the wiring added in Phase 3 Task 6 end to end, with the
external boundaries patched: MinerU itself is never called, Celery dispatch is
inline, and embedding/chunking are mocked. PostgreSQL (and the DB=1 test
Redis) are real.
"""

from __future__ import annotations

import contextlib
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import config as app_config
from app.conversion.job_store import Job, JobStore
from app.db import Document, DocumentStatus, DocumentType
from app.notifications.persistence import Notification
from app.services.task_dispatcher import get_task_dispatcher

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL (DB=1 run-tests.sh mode)",
    ),
]


# ---------------------------------------------------------------------------
# Inline dispatcher + patched Celery session maker
# ---------------------------------------------------------------------------


class InlineConversionDispatcher:
    """Runs the upload hand-off in the calling coroutine (no broker).

    Mirrors ``process_file_upload_with_document_task``: MinerU-handled files go
    to the durable conversion runner, everything else to the normal pipeline.
    """

    async def dispatch_file_processing(
        self,
        *,
        document_id: int,
        temp_path: str,
        filename: str,
        workspace_id: int,
        user_id: str,
        use_vision_llm: bool = False,
        processing_mode: str = "basic",
    ) -> None:
        from app.tasks.celery_tasks.document_tasks import (
            _mineru_handoff_applies,
            _process_file_with_document,
            _start_mineru_conversion,
        )

        with contextlib.suppress(Exception):
            if _mineru_handoff_applies(filename, use_vision_llm):
                await _start_mineru_conversion(
                    document_id=document_id,
                    temp_path=temp_path,
                    filename=filename,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    use_vision_llm=use_vision_llm,
                    processing_mode=processing_mode,
                )
            else:
                await _process_file_with_document(
                    document_id,
                    temp_path,
                    filename,
                    workspace_id,
                    user_id,
                    use_vision_llm=use_vision_llm,
                    processing_mode=processing_mode,
                )


@pytest.fixture
def conversion_uploads():
    from app.app import app

    app.dependency_overrides[get_task_dispatcher] = lambda: InlineConversionDispatcher()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_task_dispatcher, None)


@pytest.fixture
def transaction_session_maker(db_session, monkeypatch):
    """Bind Celery's session factory to the test transaction (savepoints)."""
    maker = async_sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    getter = lambda: maker  # noqa: E731
    monkeypatch.setattr("app.tasks.celery_tasks.get_celery_session_maker", getter)
    monkeypatch.setattr(
        "app.tasks.celery_tasks.document_tasks.get_celery_session_maker", getter
    )
    monkeypatch.setattr(
        "app.tasks.celery_tasks.stale_notification_cleanup_task.get_celery_session_maker",
        getter,
    )
    return maker


@pytest.fixture
def conversion_root(tmp_path, monkeypatch):
    root = tmp_path / "conversions"
    monkeypatch.setattr(app_config, "CONVERSION_WORK_DIR", str(root))
    return root


@pytest.fixture(autouse=True)
def _mock_external_boundaries(monkeypatch):
    """Keep the suite hermetic: no embedding model, chunker, Redis heartbeats."""
    import app.app  # noqa: F401  (ensure the DI container is built)

    dimension = app_config.embedding_model_instance.dimension
    monkeypatch.setattr(
        "app.indexing_pipeline.cache.cached_indexing.embed_texts",
        MagicMock(side_effect=lambda texts: [[0.1] * dimension for _ in texts]),
    )
    monkeypatch.setattr(
        "app.indexing_pipeline.cache.cached_indexing.chunk_text",
        MagicMock(return_value=["Test chunk content."]),
    )
    monkeypatch.setattr(app_config, "KNOWLEDGE_STORE_ENABLED", False)

    from app.tasks.celery_tasks import document_tasks

    monkeypatch.setattr(document_tasks, "_start_heartbeat", lambda _id: None)
    monkeypatch.setattr(document_tasks, "_stop_heartbeat", lambda _id: None)
    monkeypatch.setattr(document_tasks, "_run_heartbeat_loop", AsyncMock())


@pytest.fixture
def capture_enqueues(monkeypatch):
    """Record ``convert_document_step.apply_async`` instead of using a broker."""
    from app.tasks.celery_tasks.conversion_tasks import convert_document_step

    calls: list[tuple[tuple, float]] = []

    def fake_apply_async(args, countdown=0, **_kwargs):
        calls.append((args, countdown))

    monkeypatch.setattr(convert_document_step, "apply_async", fake_apply_async)
    return calls


class FakeResumeRedis:
    """Only ``exists`` is used by the resume sweep."""

    def __init__(self, held: set[str] | None = None) -> None:
        self.held = held or set()

    def exists(self, key: str) -> int:
        return int(key in self.held)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_document(
    db_session,
    workspace,
    user,
    *,
    status: dict,
    title: str = "conversion doc",
    unique_identifier_hash: str | None = None,
) -> Document:
    document = Document(
        title=title,
        document_type=DocumentType.FILE,
        content="Processing...",
        content_hash=f"hash-{title}",
        unique_identifier_hash=unique_identifier_hash,
        workspace_id=workspace.id,
        created_by_id=str(user.id),
        document_metadata={"FILE_NAME": title},
        status=status,
    )
    db_session.add(document)
    await db_session.flush()
    return document


def _job(document_id: int, workspace_id: int, user_id: str) -> Job:
    return Job(
        document_id=document_id,
        filename="book.pdf",
        workspace_id=workspace_id,
        user_id=user_id,
        use_vision_llm=False,
        processing_mode="basic",
        source_name="source.pdf",
        source_sha256="0" * 64,
        pages_total=10,
        batch_pages=10,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_upload_pdf_with_mineru_creates_job_and_queued_status(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    conversion_uploads,
    transaction_session_maker,
    conversion_root,
    capture_enqueues,
    monkeypatch,
):
    from tests.utils.helpers import upload_file

    monkeypatch.setattr(app_config, "ETL_SERVICE", "MINERU")
    headers = await auth(db_user)

    response = await upload_file(
        client, headers, "sample.pdf", workspace_id=db_workspace.id
    )
    assert response.status_code == 200, response.text
    document_id = response.json()["document_ids"][0]

    document = await db_session.get(Document, document_id)
    await db_session.refresh(document)
    assert document.status["state"] == "processing"
    assert document.status["stage"] == "queued"
    assert document.status["pages_done"] == 0
    assert document.status["pages_total"] == 1  # tests/fixtures/sample.pdf

    job_path = conversion_root / str(document_id) / "job.json"
    assert job_path.exists()
    job = json.loads(job_path.read_text())
    assert job["document_id"] == document_id
    assert job["pages_total"] == 1
    assert job["next_batch"] == 0

    assert capture_enqueues == [((document_id,), 0)]


async def test_stale_cleanup_ignores_converting_documents(
    db_session, db_user, db_workspace, transaction_session_maker, conversion_root
):
    from app.tasks.celery_tasks.stale_notification_cleanup_task import (
        _cleanup_stale_document_processing_notifications,
    )

    document = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status=DocumentStatus.processing(
            stage="converting", pages_done=10, pages_total=400
        ),
        title="converting mid-book",
    )
    job_dir = conversion_root / str(document.id)
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text("{}")

    await _cleanup_stale_document_processing_notifications()

    await db_session.refresh(document)
    assert document.status["state"] == "processing"
    assert document.status["stage"] == "converting"

    # The stale sweep is notification-driven; conversion creates no notification,
    # which is why it can never mark a converting document failed.
    count = await db_session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.type == "document_processing",
            Notification.notification_metadata["document_id"].astext
            == str(document.id),
        )
    )
    assert count == 0


async def test_resume_sweep_requeues_and_cleans_orphans(
    db_session, db_user, db_workspace, conversion_root
):
    from app.tasks.celery_tasks.conversion_tasks import (
        _resume_conversions,
        lock_key,
    )

    store = JobStore(conversion_root, 1024**3)
    source = conversion_root.parent / "source.pdf"
    source.write_bytes(b"%PDF-1.4 placeholder")

    orphan_id = 987654
    store.create(_job(orphan_id, db_workspace.id, str(db_user.id)), source)

    processing = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status={"state": "processing"},
        title="resume me",
    )
    store.create(_job(processing.id, db_workspace.id, str(db_user.id)), source)

    locked = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status={"state": "processing"},
        title="already locked",
    )
    store.create(_job(locked.id, db_workspace.id, str(db_user.id)), source)

    maker = async_sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    enqueued: list[int] = []
    await _resume_conversions(
        store=store,
        redis_client=FakeResumeRedis(held={lock_key(locked.id)}),
        session_maker=lambda: maker,
        enqueue=enqueued.append,
    )

    assert orphan_id not in store.list_ids()  # deleted document → dir removed
    assert set(store.list_ids()) == {processing.id, locked.id}

    await db_session.refresh(processing)
    assert processing.status["state"] == "processing"
    assert processing.status["stage"] == "queued"

    await db_session.refresh(locked)
    assert locked.status["state"] == "processing"
    assert "stage" not in locked.status

    assert enqueued == [processing.id]  # locked document is not re-queued


async def test_markdown_override_skips_extraction(
    db_session, db_user, db_workspace, transaction_session_maker, tmp_path, monkeypatch
):
    from app.tasks.celery_tasks.document_tasks import _process_file_with_document
    from app.utils.document_converters import generate_unique_identifier_hash

    document = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status={"state": "pending"},
        title="mineru output",
        # The indexing pipeline looks the upload up by this hash; without it,
        # it would create a second row instead of finishing this one.
        unique_identifier_hash=generate_unique_identifier_hash(
            DocumentType.FILE, "book.pdf", db_workspace.id
        ),
    )
    handoff = tmp_path / "handoff.pdf"
    handoff.write_bytes(b"the source bytes are not read")

    def must_not_extract(*_args, **_kwargs):
        raise AssertionError("extract_with_cache must not run for an override")

    monkeypatch.setattr("app.etl_pipeline.cache.extract_with_cache", must_not_extract)

    await _process_file_with_document(
        document.id,
        str(handoff),
        "book.pdf",
        db_workspace.id,
        str(db_user.id),
        markdown_override="# hi",
    )

    await db_session.refresh(document)
    assert document.status["state"] == "ready"
    assert "hi" in (document.content or "")
    assert not handoff.exists()  # the hand-off temp file is consumed


# ---------------------------------------------------------------------------
# Deleting a conversion in flight (a long book must not lock the user out)
# ---------------------------------------------------------------------------


class _CancelClient:
    def __init__(self) -> None:
        self.cancels: list[str] = []

    async def cancel(self, task_id: str) -> None:
        self.cancels.append(task_id)


def _mock_delete_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.tasks.celery_tasks.document_tasks.delete_document_task.delay",
        lambda *_args, **_kwargs: None,
    )


async def _make_converting_document(db_session, workspace, user):
    from app.db import DocumentStatus

    return await _make_document(
        db_session,
        workspace,
        user,
        status=DocumentStatus.processing(
            stage="converting", pages_done=10, pages_total=400
        ),
        title="long book",
    )


async def test_delete_while_converting_cancels_cleans_and_stops_the_chain(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    transaction_session_maker,
    conversion_root,
    monkeypatch,
):
    from app.conversion.job_store import JobStore
    from app.conversion.runner import StepOutcome, run_step

    _mock_delete_dispatch(monkeypatch)
    cancel_client = _CancelClient()
    monkeypatch.setattr(
        "app.conversion.mineru_client.client_from_config", lambda: cancel_client
    )

    document = await _make_converting_document(db_session, db_workspace, db_user)
    store = JobStore(conversion_root, 1024**3)
    source = conversion_root.parent / "source.pdf"
    source.write_bytes(b"%PDF-1.4 placeholder")
    job = _job(document.id, db_workspace.id, str(db_user.id))
    job.mineru_task_id = "task-1"
    store.create(job, source)
    job_dir = conversion_root / str(document.id)
    assert job_dir.is_dir()

    response = await client.delete(
        f"/api/v1/documents/{document.id}", headers=await auth(db_user)
    )

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Document deleted successfully"
    assert cancel_client.cancels == ["task-1"]
    assert not job_dir.exists()

    await db_session.refresh(document)
    assert document.status["state"] == "deleting"

    # The chain that was mid-flight wakes up, sees the document is deleting and
    # stops without recreating any state.
    outcome, _delay = await run_step(
        document.id,
        store=store,
        client=cancel_client,
        session_maker=lambda: transaction_session_maker,
    )
    assert outcome is StepOutcome.STOPPED
    assert not job_dir.exists()
    assert cancel_client.cancels == ["task-1"]


async def test_delete_while_indexing_still_conflicts(
    client, auth, db_session, db_user, db_workspace, monkeypatch
):
    from app.db import DocumentStatus

    _mock_delete_dispatch(monkeypatch)
    document = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status=DocumentStatus.processing(stage="indexing"),
        title="indexing",
    )

    response = await client.delete(
        f"/api/v1/documents/{document.id}", headers=await auth(db_user)
    )

    assert response.status_code == 409
    await db_session.refresh(document)
    assert document.status["stage"] == "indexing"


async def test_delete_stale_pending_ghost_is_allowed(
    client, auth, db_session, db_user, db_workspace, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    _mock_delete_dispatch(monkeypatch)
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    document = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status={"state": "pending", "updated_at": stale},
        title="stuck pending",
    )

    response = await client.delete(
        f"/api/v1/documents/{document.id}", headers=await auth(db_user)
    )

    assert response.status_code == 200, response.text


async def test_delete_fresh_pending_still_conflicts(
    client, auth, db_session, db_user, db_workspace, monkeypatch
):
    from app.db import DocumentStatus

    _mock_delete_dispatch(monkeypatch)
    document = await _make_document(
        db_session,
        db_workspace,
        db_user,
        status=DocumentStatus.pending(),
        title="fresh pending",
    )

    response = await client.delete(
        f"/api/v1/documents/{document.id}", headers=await auth(db_user)
    )

    assert response.status_code == 409
