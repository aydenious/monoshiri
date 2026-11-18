"""POST /api/v1/documents/url: identity, SSRF refusal, and the one-shot save.

External boundaries are patched: ``safe_get``, the YouTube transcript call and
the Celery dispatcher are fakes; PostgreSQL and the indexing pipeline are real.
The PDF-link test also drives the conversion hand-off, which is the step that
must not overwrite the row's URL identity or citation metadata.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.app import app
from app.config import config as app_config
from app.db import Document, DocumentStatus, DocumentType, Folder, Workspace
from app.routes.url_documents_routes import get_url_ingest_dispatcher
from app.services.url_ingest import extract_readable, youtube_oembed_url
from app.utils.crawl.safe_fetch import FetchedPage, UnsafeUrlError
from app.utils.document_converters import generate_unique_identifier_hash

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL (DB=1 run-tests.sh mode)",
    ),
]

URL_ENDPOINT = "/api/v1/documents/url"
PAGE_URL = "https://example.com/article"
PAGE_HTML = b"""<!DOCTYPE html><html><head><title>Article</title></head>
<body><main><article><h1>Saved page</h1>
<p>This paragraph is long enough for the readability extractor to keep it.</p>
</article></main></body></html>"""
JS_ONLY_HTML = (
    b"<html><head><title>App</title></head><body>"
    b'<div id="root"></div><script>var x = 1;</script>'
    b"</body></html>"
)


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------


class FakeFetch:
    """Records requested URLs and returns queued responses (or raises)."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, FetchedPage] = {}
        self.errors: dict[str, Exception] = {}
        # The task is the only fetcher now: the route must never download.
        monkeypatch.setattr("app.tasks.celery_tasks.url_tasks.safe_get", self._get)

    async def _get(self, url: str, **_kwargs) -> FetchedPage:
        self.calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url not in self.responses:
            raise AssertionError(f"unexpected fetch: {url}")
        return self.responses[url]

    def add(
        self,
        url: str,
        *,
        content_type: str = "text/html; charset=utf-8",
        body: bytes = b"",
    ) -> None:
        self.responses[url] = FetchedPage(
            final_url=url, status=200, content_type=content_type, body=body
        )

    def fail(self, url: str, exc: Exception) -> None:
        self.errors[url] = exc


class RecordingNoopDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch_url_ingest(self, **kwargs) -> None:
        self.calls.append(kwargs)


class InlineUrlDispatcher:
    """Runs ``ingest_url_document`` in the caller instead of through Celery."""

    async def dispatch_url_ingest(self, **kwargs) -> None:
        from app.tasks.celery_tasks.url_tasks import ingest_url_document

        await ingest_url_document(**kwargs)


@pytest.fixture
def fake_fetch(monkeypatch) -> FakeFetch:
    return FakeFetch(monkeypatch)


@pytest.fixture
def use_dispatcher():
    def install(dispatcher):
        app.dependency_overrides[get_url_ingest_dispatcher] = lambda: dispatcher
        return dispatcher

    yield install
    app.dependency_overrides.pop(get_url_ingest_dispatcher, None)


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
        "app.tasks.celery_tasks.url_tasks.get_celery_session_maker", getter
    )
    return maker


@pytest.fixture(autouse=True)
def _public_urls_only(monkeypatch):
    """No real DNS in tests: the guard is unit-tested in its own module.

    The route's answer to a refused host is tested explicitly below; this
    fixture only keeps the success-path tests offline and deterministic.
    """
    monkeypatch.setattr(
        "app.routes.url_documents_routes.is_publicly_routable", lambda _url: True
    )


@pytest.fixture(autouse=True)
def _mock_external_boundaries(monkeypatch):
    """No embedding model, chunker or knowledge-store side effects."""
    import app.app  # noqa: F401  (build the DI container)

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


def _headers(auth, user):
    return auth(user)


async def _workspace_documents(db_session, workspace_id: int) -> list[Document]:
    result = await db_session.execute(
        select(Document).where(Document.workspace_id == workspace_id)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Route behaviour
# ---------------------------------------------------------------------------


async def test_post_url_creates_pending_document_202(
    client, auth, db_session, db_user, db_workspace, fake_fetch, use_dispatcher
):
    fake_fetch.add(PAGE_URL, body=PAGE_HTML)
    dispatcher = use_dispatcher(RecordingNoopDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    assert document.status["state"] == "pending"
    assert document.document_type == DocumentType.CRAWLED_URL
    assert document.document_metadata == {"url": PAGE_URL, "source_url": PAGE_URL}
    # The title is provisional until the task knows the real one.
    assert document.title == PAGE_URL
    assert dispatcher.calls and dispatcher.calls[0]["kind"] == "page_or_pdf"
    # Classification in the request is cheap: the page is fetched by the task.
    assert fake_fetch.calls == []


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://192.168.1.10/"])
async def test_post_url_private_address_rejected_400(
    client, auth, db_session, db_user, db_workspace, url, monkeypatch
):
    """Explicit refusal of a private host (no DNS: the guard itself is faked)."""
    monkeypatch.setattr(
        "app.routes.url_documents_routes.is_publicly_routable", lambda _url: False
    )
    before = await _workspace_documents(db_session, db_workspace.id)

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": url},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "URL is not publicly reachable"
    assert await _workspace_documents(db_session, db_workspace.id) == before


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        ("ftp://example.com/file", "Only http and https URLs can be saved"),
        ("file:///etc/passwd", "Only http and https URLs can be saved"),
        ("not a url", "Invalid URL"),
        ("https://", "Invalid URL"),
        ("https://example.com/" + "a" * 2084, "Invalid URL"),
    ],
)
async def test_post_url_refused_shape_is_400(
    client, auth, db_session, db_user, db_workspace, url, detail
):
    """A refused URL is a declared 400, never FastAPI's 422 error list."""
    before = await _workspace_documents(db_session, db_workspace.id)

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": url},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["detail"] == detail
    # The declared UrlRejected shape: a plain string, never a validation list.
    assert isinstance(body["detail"], str)
    assert await _workspace_documents(db_session, db_workspace.id) == before


async def test_post_url_without_permission_403(client, auth, make_user, db_workspace):
    outsider = await make_user()

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, outsider),
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Folder ownership: a folder id is only accepted from this workspace
# ---------------------------------------------------------------------------


async def _make_folder(db_session, workspace_id: int, name: str = "Docs") -> Folder:
    folder = Folder(name=name, position="a0", workspace_id=workspace_id)
    db_session.add(folder)
    await db_session.flush()
    return folder


async def _make_foreign_folder(db_session, user) -> Folder:
    other = Workspace(name="Other Space", user_id=user.id)
    db_session.add(other)
    await db_session.flush()
    return await _make_folder(db_session, other.id, "Private")


async def test_foreign_folder_is_404_and_nothing_is_written(
    client, auth, db_session, db_user, db_workspace, use_dispatcher
):
    foreign = await _make_foreign_folder(db_session, db_user)
    dispatcher = use_dispatcher(RecordingNoopDispatcher())
    before = await _workspace_documents(db_session, db_workspace.id)

    response = await client.post(
        URL_ENDPOINT,
        json={
            "workspace_id": db_workspace.id,
            "url": PAGE_URL,
            "folder_id": foreign.id,
        },
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Folder not found"
    assert await _workspace_documents(db_session, db_workspace.id) == before
    assert dispatcher.calls == []


async def test_foreign_folder_on_the_retry_path_is_404(
    client, auth, db_session, db_user, db_workspace, use_dispatcher
):
    """A failed row must not be re-filed into another workspace's folder."""
    foreign = await _make_foreign_folder(db_session, db_user)
    unique_hash = generate_unique_identifier_hash(
        DocumentType.CRAWLED_URL, PAGE_URL, db_workspace.id
    )
    existing = Document(
        title=PAGE_URL,
        document_type=DocumentType.CRAWLED_URL,
        document_metadata={"url": PAGE_URL, "source_url": PAGE_URL},
        content="Processing...",
        content_hash=unique_hash,
        unique_identifier_hash=unique_hash,
        workspace_id=db_workspace.id,
        created_by_id=db_user.id,
        status=DocumentStatus.failed("Could not fetch the URL"),
    )
    db_session.add(existing)
    await db_session.flush()
    dispatcher = use_dispatcher(RecordingNoopDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={
            "workspace_id": db_workspace.id,
            "url": PAGE_URL,
            "folder_id": foreign.id,
        },
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 404, response.text
    await db_session.refresh(existing)
    assert existing.status["state"] == "failed"
    assert existing.folder_id is None
    assert dispatcher.calls == []


async def test_missing_folder_is_404(
    client, auth, db_session, db_user, db_workspace, use_dispatcher
):
    dispatcher = use_dispatcher(RecordingNoopDispatcher())
    before = await _workspace_documents(db_session, db_workspace.id)

    response = await client.post(
        URL_ENDPOINT,
        json={
            "workspace_id": db_workspace.id,
            "url": PAGE_URL,
            "folder_id": 999_999,
        },
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Folder not found"
    assert await _workspace_documents(db_session, db_workspace.id) == before
    assert dispatcher.calls == []


async def test_folder_in_the_same_workspace_is_set(
    client, auth, db_session, db_user, db_workspace, use_dispatcher
):
    folder = await _make_folder(db_session, db_workspace.id)
    use_dispatcher(RecordingNoopDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={
            "workspace_id": db_workspace.id,
            "url": PAGE_URL,
            "folder_id": folder.id,
        },
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert document.folder_id == folder.id


# ---------------------------------------------------------------------------
# Dispatch failure after the commit must leave a retryable row
# ---------------------------------------------------------------------------


class FailingDispatcher:
    async def dispatch_url_ingest(self, **_kwargs) -> None:
        raise RuntimeError("broker is down")


async def test_dispatch_failure_marks_the_row_failed_and_returns_500(
    client, auth, db_session, db_user, db_workspace, use_dispatcher
):
    use_dispatcher(FailingDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 500
    documents = await _workspace_documents(db_session, db_workspace.id)
    assert len(documents) == 1
    await db_session.refresh(documents[0])
    assert documents[0].status["state"] == "failed"
    assert documents[0].status["reason"] == "Could not save the URL"


async def test_a_non_unique_integrity_error_is_a_500(
    client, auth, db_session, db_user, db_workspace, use_dispatcher, monkeypatch
):
    """Only the unique-hash clash is the race; any other integrity error is 500."""
    from sqlalchemy.exc import IntegrityError

    async def broken_commit(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO documents ...",
            {},
            Exception("insert or update violates foreign key constraint"),
        )

    use_dispatcher(RecordingNoopDispatcher())
    monkeypatch.setattr(db_session, "commit", broken_commit)

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 500
    assert "foreign key" not in response.text


async def test_duplicate_ready_url_returns_existing(
    client, auth, db_session, db_user, db_workspace, use_dispatcher, monkeypatch
):
    unique_hash = generate_unique_identifier_hash(
        DocumentType.CRAWLED_URL, PAGE_URL, db_workspace.id
    )
    existing = Document(
        title=PAGE_URL,
        document_type=DocumentType.CRAWLED_URL,
        document_metadata={"url": PAGE_URL, "source_url": PAGE_URL},
        content="Already saved",
        content_hash="existing-content-hash",
        unique_identifier_hash=unique_hash,
        workspace_id=db_workspace.id,
        created_by_id=db_user.id,
        status=DocumentStatus.ready(),
    )
    db_session.add(existing)
    await db_session.flush()
    dispatcher = use_dispatcher(RecordingNoopDispatcher())

    async def must_not_fetch(*_args, **_kwargs):
        raise AssertionError("a duplicate READY URL must not be fetched again")

    monkeypatch.setattr("app.tasks.celery_tasks.url_tasks.safe_get", must_not_fetch)

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 200, response.text
    assert response.json()["document_id"] == existing.id
    assert dispatcher.calls == []


async def test_youtube_share_link_variants_hit_one_row(
    client, auth, db_session, db_user, db_workspace, use_dispatcher
):
    """The iOS share sheet's many forms of one video are one document."""
    first = "https://youtu.be/dQw4w9WgXcQ?si=one"
    second = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=two&t=30"
    dispatcher = use_dispatcher(RecordingNoopDispatcher())

    created = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": first},
        headers=await _headers(auth, db_user),
    )
    repeated = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": second},
        headers=await _headers(auth, db_user),
    )

    assert created.status_code == 202, created.text
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["document_id"] == created.json()["document_id"]
    assert len(dispatcher.calls) == 1  # one row, one ingest loop

    document = await db_session.get(Document, created.json()["document_id"])
    await db_session.refresh(document)
    assert document.document_type == DocumentType.YOUTUBE_VIDEO
    assert (
        document.document_metadata["url"]
        == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )


@pytest.mark.parametrize(
    "status",
    [
        DocumentStatus.pending(),
        DocumentStatus.processing(stage=DocumentStatus.STAGE_INDEXING),
    ],
    ids=["pending", "processing"],
)
async def test_repost_of_in_flight_url_returns_existing_without_dispatch(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    use_dispatcher,
    monkeypatch,
    status,
):
    """One loop per row: pending/processing reuse the id and dispatch nothing."""
    unique_hash = generate_unique_identifier_hash(
        DocumentType.CRAWLED_URL, PAGE_URL, db_workspace.id
    )
    existing = Document(
        title=PAGE_URL,
        document_type=DocumentType.CRAWLED_URL,
        document_metadata={"url": PAGE_URL, "source_url": PAGE_URL},
        content="Processing...",
        content_hash=unique_hash,
        unique_identifier_hash=unique_hash,
        workspace_id=db_workspace.id,
        created_by_id=db_user.id,
        status=status,
    )
    db_session.add(existing)
    await db_session.flush()
    dispatcher = use_dispatcher(RecordingNoopDispatcher())

    async def must_not_fetch(*_args, **_kwargs):
        raise AssertionError("an in-flight row must not be dispatched again")

    monkeypatch.setattr("app.tasks.celery_tasks.url_tasks.safe_get", must_not_fetch)

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 200, response.text
    assert response.json()["document_id"] == existing.id
    assert dispatcher.calls == []


async def test_repost_of_stalled_in_flight_url_is_retried(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    use_dispatcher,
):
    """A pending row with no sign of life for 15+ min has no loop: retry it."""
    unique_hash = generate_unique_identifier_hash(
        DocumentType.CRAWLED_URL, PAGE_URL, db_workspace.id
    )
    stale = DocumentStatus.pending()
    stale["updated_at"] = "2026-01-01T00:00:00Z"
    existing = Document(
        title=PAGE_URL,
        document_type=DocumentType.CRAWLED_URL,
        document_metadata={"url": PAGE_URL, "source_url": PAGE_URL},
        content="Processing...",
        content_hash=unique_hash,
        unique_identifier_hash=unique_hash,
        workspace_id=db_workspace.id,
        created_by_id=db_user.id,
        status=stale,
    )
    db_session.add(existing)
    await db_session.flush()
    dispatcher = use_dispatcher(RecordingNoopDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    assert response.json()["document_id"] == existing.id
    assert dispatcher.calls and dispatcher.calls[0]["document_id"] == existing.id


async def test_repost_of_failed_url_retries_and_supersedes_old_job(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    use_dispatcher,
    monkeypatch,
    tmp_path,
):
    """A failed row is retried; the old conversion job is dropped first."""
    from app.conversion.job_store import Job, JobStore

    unique_hash = generate_unique_identifier_hash(
        DocumentType.CRAWLED_URL, PAGE_URL, db_workspace.id
    )
    existing = Document(
        title=PAGE_URL,
        document_type=DocumentType.CRAWLED_URL,
        document_metadata={"url": PAGE_URL, "source_url": PAGE_URL},
        content="Processing...",
        content_hash=unique_hash,
        unique_identifier_hash=unique_hash,
        workspace_id=db_workspace.id,
        created_by_id=db_user.id,
        status=DocumentStatus.failed("Could not fetch the URL"),
    )
    db_session.add(existing)
    await db_session.flush()

    # An earlier PDF conversion left a live job directory for this document.
    store = JobStore(tmp_path / "conversions", 1024**3)
    job = Job(
        document_id=existing.id,
        filename="old.pdf",
        workspace_id=db_workspace.id,
        user_id=str(db_user.id),
        use_vision_llm=False,
        processing_mode="basic",
        source_name="source.pdf",
        source_sha256="0" * 64,
        pages_total=1,
        batch_pages=1,
        mineru_task_id="old-task",
    )
    source = tmp_path / "old.pdf"
    source.write_bytes(b"%PDF-1.4")
    store.create(job, source)
    assert (tmp_path / "conversions" / str(existing.id)).is_dir()

    monkeypatch.setattr(
        "app.routes.url_documents_routes.job_store_from_config", lambda: store
    )
    dispatcher = use_dispatcher(RecordingNoopDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    assert response.json()["document_id"] == existing.id
    assert dispatcher.calls and dispatcher.calls[0]["document_id"] == existing.id
    assert dispatcher.calls[0]["kind"] == "page_or_pdf"
    assert not (tmp_path / "conversions" / str(existing.id)).exists()
    assert store.load(existing.id) is None

    document = await db_session.get(Document, existing.id)
    await db_session.refresh(document)
    assert document.status["state"] == "pending"


async def test_save_failure_never_echoes_the_exception(
    client, auth, db_session, db_user, db_workspace, use_dispatcher, monkeypatch
):
    async def broken_commit(*_args, **_kwargs):
        raise RuntimeError("password=hunter2 connection refused")

    monkeypatch.setattr(db_session, "commit", broken_commit)
    dispatcher = use_dispatcher(RecordingNoopDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 500
    # The error middleware replaces even a curated detail with a generic 500
    # message; the point is that no exception text reaches the client.
    assert "hunter2" not in response.text
    assert "connection refused" not in response.text
    assert dispatcher.calls == []


async def test_page_url_becomes_ready_with_source_url(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    fake_fetch,
    use_dispatcher,
    transaction_session_maker,
):
    fake_fetch.add(PAGE_URL, body=PAGE_HTML)
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert document.status["state"] == "ready"
    assert document.document_type == DocumentType.CRAWLED_URL
    assert document.document_metadata["url"] == PAGE_URL
    assert document.document_metadata["source_url"] == PAGE_URL
    assert "long enough for the readability extractor" in (document.content or "")
    # The real page title from the HTML already in memory, not the raw URL.
    assert document.title == "Saved page"
    # Exactly one download: the task's, not a route-side sniff plus a task fetch.
    assert fake_fetch.calls == [PAGE_URL]


async def test_page_without_a_title_falls_back_to_the_url(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    fake_fetch,
    use_dispatcher,
    transaction_session_maker,
):
    url = "https://example.com/untitled"
    fake_fetch.add(
        url,
        body=(
            b"<html><body><article><p>This paragraph is long enough for the "
            b"readability extractor to keep it as content.</p></article>"
            b"</body></html>"
        ),
    )
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": url},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert document.status["state"] == "ready"
    assert document.title == url


async def test_redirect_to_private_address_fails_row_with_reason(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    fake_fetch,
    use_dispatcher,
    transaction_session_maker,
):
    """The redirect is followed in the task, where it fails the row (not a 422)."""
    fake_fetch.fail(PAGE_URL, UnsafeUrlError("http://192.168.1.10/"))
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert document.status["state"] == "failed"
    assert document.status["reason"] == "URL is not publicly reachable"


async def test_js_only_page_fails_with_reason(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    fake_fetch,
    use_dispatcher,
    transaction_session_maker,
):
    fake_fetch.add(PAGE_URL, body=JS_ONLY_HTML)
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert document.status["state"] == "failed"
    assert "No readable text" in document.status["reason"]


async def test_youtube_url_uses_transcript(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    fake_fetch,
    use_dispatcher,
    transaction_session_maker,
    monkeypatch,
):
    from app.proprietary.platforms.youtube.schemas import SubtitleTrack
    from app.tasks.celery_tasks import url_tasks

    url = "https://youtu.be/dQw4w9WgXcQ"
    canonical = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    seen_video_ids: list[str] = []

    async def fake_fetch_subtitles(video_id: str, **_kwargs):
        seen_video_ids.append(video_id)
        return [
            SubtitleTrack(
                srtUrl=None,
                type="auto_generated",
                language="en",
                srt=(
                    "1\n00:00:00,000 --> 00:00:01,000\nHello transcript\n\n"
                    "2\n00:00:01,000 --> 00:00:02,000\nMore words\n"
                ),
            )
        ]

    fake_fetch.add(
        youtube_oembed_url("dQw4w9WgXcQ"),
        content_type="application/json",
        body=b'{"title": "Never Gonna Give You Up"}',
    )
    monkeypatch.setattr(url_tasks, "fetch_subtitles", fake_fetch_subtitles)
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": url},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert seen_video_ids == ["dQw4w9WgXcQ"]
    assert document.status["state"] == "ready"
    assert document.document_type == DocumentType.YOUTUBE_VIDEO
    # The stored identity is the canonical URL, not the share link.
    assert document.document_metadata["url"] == canonical
    # The video's title comes from oEmbed (through safe_get), not the URL.
    assert document.title == "Never Gonna Give You Up"
    assert "Hello transcript" in (document.content or "")


async def test_youtube_without_transcript_fails_with_curated_reason(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    use_dispatcher,
    transaction_session_maker,
    monkeypatch,
):
    from app.tasks.celery_tasks import url_tasks

    url = "https://youtu.be/noTranscript"

    async def no_tracks(_video_id: str, **_kwargs):
        return None

    monkeypatch.setattr(url_tasks, "fetch_subtitles", no_tracks)
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": url},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert document.status["state"] == "failed"
    # Curated string, never the internal exception text.
    assert document.status["reason"] == "No transcript available for this video"


async def test_pdf_link_document_keeps_source_url(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    fake_fetch,
    use_dispatcher,
    transaction_session_maker,
    monkeypatch,
    tmp_path,
):
    from app.conversion.job_store import Job, JobStore
    from app.conversion.runner import StepOutcome, _finish_conversion
    from app.tasks.celery_tasks import url_tasks
    from app.tasks.document_processors.base import get_current_timestamp

    url = "https://example.com/paper.pdf"
    fake_fetch.add(url, content_type="application/pdf", body=b"%PDF-1.4 test")
    started: list[dict] = []

    async def fake_start_conversion(session, **kwargs):
        started.append(kwargs)
        document = await session.get(Document, kwargs["document_id"])
        document.status = DocumentStatus.processing(stage=DocumentStatus.STAGE_QUEUED)
        document.updated_at = get_current_timestamp()
        await session.commit()
        # The real runner consumes (deletes) the temp file.
        Path(kwargs["temp_path"]).unlink(missing_ok=True)

    monkeypatch.setattr(url_tasks, "start_conversion", fake_start_conversion)
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": url},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)

    assert fake_fetch.calls == [url]  # one download, in the task
    assert started and started[0]["filename"].endswith(".pdf")
    assert started[0]["document_id"] == document.id
    assert document.document_type == DocumentType.CRAWLED_URL
    assert document.document_metadata["url"] == url
    assert document.document_metadata["source_url"] == url
    # The PDF's title is its file name, known before MinerU finishes.
    assert document.title == "paper"

    # Drive the conversion's final hand-off: this is the indexing step that
    # used to re-identify a PDF as a FILE and wipe citation metadata.
    store = JobStore(tmp_path / "conversions", 1024**3)
    job = Job(
        document_id=document.id,
        filename="paper.pdf",
        workspace_id=db_workspace.id,
        user_id=str(db_user.id),
        use_vision_llm=False,
        processing_mode="basic",
        source_name="source.pdf",
        source_sha256="0" * 64,
        pages_total=1,
        batch_pages=1,
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4")
    store.create(job, source)
    store.write_batch(job, 0, "# Converted paper\n\nBody text from MinerU.")

    outcome, _delay = await _finish_conversion(
        job, document.id, store, lambda: transaction_session_maker
    )

    assert outcome == StepOutcome.DONE
    await db_session.refresh(document)
    assert document.status["state"] == "ready"
    assert document.document_type == DocumentType.CRAWLED_URL
    assert document.document_metadata["url"] == url
    assert document.document_metadata["source_url"] == url
    assert document.title == "paper"  # the hand-off keeps the PDF's title
    assert "Converted paper" in (document.content or "")


async def test_pdf_url_that_returns_html_is_extracted_as_a_page(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    fake_fetch,
    use_dispatcher,
    transaction_session_maker,
    monkeypatch,
):
    """A paywalled ``.pdf`` URL that answers with a login page is not a PDF."""
    from app.tasks.celery_tasks import url_tasks

    url = "https://example.com/paper.pdf"
    fake_fetch.add(url, content_type="application/pdf", body=PAGE_HTML)

    async def must_not_convert(*_args, **_kwargs):
        raise AssertionError("HTML bytes must not be handed to MinerU")

    monkeypatch.setattr(url_tasks, "start_conversion", must_not_convert)
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": url},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert document.status["state"] == "ready"
    assert "long enough for the readability extractor" in (document.content or "")


async def test_duplicate_content_keeps_the_pipelines_reason(
    client,
    auth,
    db_session,
    db_user,
    db_workspace,
    fake_fetch,
    use_dispatcher,
    transaction_session_maker,
):
    """A repeat save of content already indexed keeps "Duplicate content"."""
    fake_fetch.add(PAGE_URL, body=PAGE_HTML)
    markdown = extract_readable(PAGE_HTML, PAGE_URL)
    content_hash = hashlib.sha256(f"{db_workspace.id}:{markdown}".encode()).hexdigest()
    duplicate = Document(
        title="Already saved article",
        document_type=DocumentType.CRAWLED_URL,
        document_metadata={"url": "https://elsewhere.example/copy"},
        content="The same words, saved earlier.",
        content_hash=content_hash,
        unique_identifier_hash="duplicate-of-another-url",
        workspace_id=db_workspace.id,
        created_by_id=db_user.id,
        status=DocumentStatus.ready(),
    )
    db_session.add(duplicate)
    await db_session.flush()
    use_dispatcher(InlineUrlDispatcher())

    response = await client.post(
        URL_ENDPOINT,
        json={"workspace_id": db_workspace.id, "url": PAGE_URL},
        headers=await _headers(auth, db_user),
    )

    assert response.status_code == 202, response.text
    document = await db_session.get(Document, response.json()["document_id"])
    await db_session.refresh(document)
    assert document.status["state"] == "failed"
    assert document.status["reason"] == (
        "Duplicate content: matches 'Already saved article'"
    )
