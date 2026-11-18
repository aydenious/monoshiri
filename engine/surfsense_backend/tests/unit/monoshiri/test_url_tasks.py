"""User-visible URL-save failure reasons are a curated, fixed vocabulary.

The task must never put raw exception text (SQL, driver or library messages)
into ``documents.status.reason``; the real exception is only logged.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.url_ingest import NoReadableTextError
from app.tasks.celery_tasks import url_tasks
from app.utils.crawl.safe_fetch import FetchError, UnsafeUrlError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (UnsafeUrlError("http://192.168.1.10/"), "URL is not publicly reachable"),
        (FetchError("too large"), "File is too large"),
        (
            FetchError("fetch failed with HTTP 404: https://x/"),
            "Could not fetch the URL",
        ),
        (httpx.ConnectTimeout("timed out"), "Could not fetch the URL"),
        (
            NoReadableTextError("No readable text (page may need JavaScript)"),
            "No readable text (page may need JavaScript)",
        ),
        (
            url_tasks.NoTranscriptError("dQw4w9WgXcQ"),
            "No transcript available for this video",
        ),
        (ValueError("relation documents does not exist"), "Could not save the URL"),
        (RuntimeError("kaboom"), "Could not save the URL"),
    ],
)
def test_failure_reason_is_curated(exc, expected):
    assert url_tasks._failure_reason(exc) == expected


def test_failure_reason_never_echoes_raw_text():
    secret = "password=hunter2 host=10.0.0.5"
    reason = url_tasks._failure_reason(RuntimeError(secret))
    assert secret not in reason


# ---------------------------------------------------------------------------
# Stage timing and temp-file cleanup
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, document) -> None:
        self.document = document
        self.commits = 0

    async def get(self, _model, document_id, **_kwargs):
        return self.document if document_id == self.document.id else None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


class _FakeSessionCtx:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_exc) -> bool:
        return False


async def test_stage_is_set_only_after_fetch_and_extract(monkeypatch):
    """The row stays pending through the download; ``indexing`` means indexing."""
    from types import SimpleNamespace

    from app.db import DocumentStatus
    from app.utils.crawl.safe_fetch import FetchedPage

    document = SimpleNamespace(
        id=7,
        status=DocumentStatus.pending(),
        updated_at=None,
        workspace_id=1,
        folder_id=None,
    )
    session = _FakeSession(document)
    monkeypatch.setattr(
        url_tasks, "get_celery_session_maker", lambda: lambda: _FakeSessionCtx(session)
    )
    observed: list[tuple[str, dict]] = []

    async def fake_get(url: str, **_kwargs) -> FetchedPage:
        observed.append(("fetch", dict(document.status)))
        return FetchedPage(
            final_url=url, status=200, content_type="text/html", body=b"<html/>"
        )

    def fake_extract(_html: bytes, _url: str) -> str:
        observed.append(("extract", dict(document.status)))
        return "# Heading"

    async def fake_index(
        _session, _document, _markdown, _url, *, title, user_id
    ) -> None:
        observed.append(("index", dict(document.status)))

    monkeypatch.setattr(url_tasks, "safe_get", fake_get)
    monkeypatch.setattr(url_tasks.url_ingest, "extract_readable", fake_extract)
    monkeypatch.setattr(url_tasks, "_index_markdown", fake_index)

    await url_tasks.ingest_url_document(
        7, "https://example.com/a", "page_or_pdf", 1, "user"
    )

    assert [name for name, _status in observed] == ["fetch", "extract", "index"]
    for _name, status in observed:
        assert status["state"] == "pending"
        assert "stage" not in status


async def test_pdf_temp_file_removed_when_start_conversion_raises(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from app.utils.crawl.safe_fetch import FetchedPage

    temp = tmp_path / "leak.pdf"
    monkeypatch.setattr(url_tasks, "_shared_tmp_path", lambda: temp)
    page = FetchedPage(
        final_url="https://example.com/p.pdf",
        status=200,
        content_type="application/pdf",
        body=b"%PDF-1.4",
    )

    async def boom(*_args, **_kwargs):
        raise RuntimeError("conversion work dir is full")

    monkeypatch.setattr(url_tasks, "start_conversion", boom)
    monkeypatch.setattr(
        "app.conversion.job_store.job_store_from_config", lambda: object()
    )
    monkeypatch.setattr(
        "app.conversion.mineru_client.client_from_config", lambda: object()
    )

    document = SimpleNamespace(id=1, workspace_id=2, title="")
    with pytest.raises(RuntimeError):
        await url_tasks._ingest_pdf(
            _FakeSession(document),
            document,
            "https://example.com/p.pdf",
            user_id="user",
            page=page,
        )

    assert not temp.exists()


# ---------------------------------------------------------------------------
# Transcripts: any language, and a bounded wait
# ---------------------------------------------------------------------------


def _track(language: str, srt: str):
    from app.proprietary.platforms.youtube.schemas import SubtitleTrack

    return SubtitleTrack(srtUrl=None, type="auto_generated", language=language, srt=srt)


async def test_non_english_transcript_is_used_via_any_language(monkeypatch):
    """English first, then whatever the video offers (here: Japanese only)."""
    from types import SimpleNamespace

    from app.db import DocumentStatus
    from app.utils.crawl.safe_fetch import FetchedPage

    document = SimpleNamespace(
        id=3,
        status=DocumentStatus.pending(),
        workspace_id=1,
        folder_id=None,
        document_type=None,
        title="https://www.youtube.com/watch?v=jaVideo01",
    )
    asked: list[str] = []

    async def fake_fetch_subtitles(_video_id: str, *, language: str = "en"):
        asked.append(language)
        if language != "any":
            return None
        return [_track("ja", "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n")]

    async def fake_oembed(url: str, **_kwargs) -> FetchedPage:
        assert "youtube.com/oembed" in url
        return FetchedPage(
            final_url=url,
            status=200,
            content_type="application/json",
            body=b'{"title": "Japanese talk"}',
        )

    indexed: dict[str, str] = {}

    async def fake_index(_session, _document, markdown, _url, *, title, user_id):
        indexed["markdown"] = markdown
        indexed["title"] = title

    monkeypatch.setattr(url_tasks, "fetch_subtitles", fake_fetch_subtitles)
    monkeypatch.setattr(url_tasks, "safe_get", fake_oembed)
    monkeypatch.setattr(url_tasks, "_index_markdown", fake_index)

    await url_tasks._ingest_youtube(
        _FakeSession(document), document, document.title, user_id="user"
    )

    assert asked == ["en", "any"]
    assert "こんにちは" in indexed["markdown"]
    assert indexed["title"] == "Japanese talk"


async def test_transcript_timeout_is_a_curated_failure(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.db import DocumentStatus

    async def never_returns(_video_id: str, **_kwargs):
        await asyncio.sleep(10)
        return None

    monkeypatch.setattr(url_tasks, "fetch_subtitles", never_returns)
    monkeypatch.setattr(url_tasks, "TRANSCRIPT_TIMEOUT_SECONDS", 0.01)

    document = SimpleNamespace(
        id=11,
        status=DocumentStatus.pending(),
        workspace_id=1,
        folder_id=None,
        document_type=None,
        title="https://www.youtube.com/watch?v=slowVideo01",
    )
    with pytest.raises(url_tasks.NoTranscriptError):
        await url_tasks._ingest_youtube(
            _FakeSession(document), document, document.title, user_id="user"
        )


# ---------------------------------------------------------------------------
# PDF vs page: the bytes decide, on both classifications
# ---------------------------------------------------------------------------


async def test_pdf_path_that_returns_html_goes_to_extraction(monkeypatch):
    from types import SimpleNamespace

    from app.db import DocumentStatus
    from app.utils.crawl.safe_fetch import FetchedPage

    document = SimpleNamespace(id=9, status=DocumentStatus.pending(), workspace_id=1)
    session = _FakeSession(document)
    monkeypatch.setattr(
        url_tasks, "get_celery_session_maker", lambda: lambda: _FakeSessionCtx(session)
    )

    async def fake_get(url: str, **_kwargs) -> FetchedPage:
        return FetchedPage(
            final_url=url,
            status=200,
            content_type="application/pdf",
            body=b"<html><body>Please sign in</body></html>",
        )

    calls: list[str] = []

    async def fake_page(*_args, **_kwargs):
        calls.append("page")

    async def fake_pdf(*_args, **_kwargs):
        calls.append("pdf")

    monkeypatch.setattr(url_tasks, "safe_get", fake_get)
    monkeypatch.setattr(url_tasks, "_ingest_page", fake_page)
    monkeypatch.setattr(url_tasks, "_ingest_pdf", fake_pdf)

    await url_tasks.ingest_url_document(
        9, "https://example.com/paper.pdf", "pdf", 1, "user"
    )

    assert calls == ["page"]


async def test_octet_stream_pdf_goes_to_the_pdf_path(monkeypatch):
    from types import SimpleNamespace

    from app.db import DocumentStatus
    from app.utils.crawl.safe_fetch import FetchedPage

    document = SimpleNamespace(id=10, status=DocumentStatus.pending(), workspace_id=1)
    session = _FakeSession(document)
    monkeypatch.setattr(
        url_tasks, "get_celery_session_maker", lambda: lambda: _FakeSessionCtx(session)
    )

    async def fake_get(url: str, **_kwargs) -> FetchedPage:
        return FetchedPage(
            final_url=url,
            status=200,
            content_type="application/octet-stream",
            body=b"%PDF-1.7\n...",
        )

    calls: list[str] = []

    async def fake_page(*_args, **_kwargs):
        calls.append("page")

    async def fake_pdf(*_args, **_kwargs):
        calls.append("pdf")

    monkeypatch.setattr(url_tasks, "safe_get", fake_get)
    monkeypatch.setattr(url_tasks, "_ingest_page", fake_page)
    monkeypatch.setattr(url_tasks, "_ingest_pdf", fake_pdf)

    await url_tasks.ingest_url_document(
        10, "https://example.com/download", "page_or_pdf", 1, "user"
    )

    assert calls == ["pdf"]


# ---------------------------------------------------------------------------
# Status writes: keep a committed pipeline reason, never touch "deleting"
# ---------------------------------------------------------------------------


async def test_mark_failed_keeps_a_committed_pipeline_reason():
    from types import SimpleNamespace

    from app.db import DocumentStatus

    reason = "Duplicate content: matches 'Already saved video'"
    document = SimpleNamespace(
        id=4, status=DocumentStatus.failed(reason), updated_at=None
    )
    session = _FakeSession(document)

    await url_tasks._mark_failed(session, 4, "Could not save the URL")

    assert document.status["reason"] == reason
    assert session.commits == 0


async def test_mark_failed_leaves_a_deleting_row_alone():
    from types import SimpleNamespace

    document = SimpleNamespace(id=5, status={"state": "deleting"}, updated_at=None)
    session = _FakeSession(document)

    await url_tasks._mark_failed(session, 5, "Could not save the URL")

    assert document.status == {"state": "deleting"}
    assert session.commits == 0


async def test_index_markdown_stops_when_the_row_is_deleting(monkeypatch):
    from types import SimpleNamespace

    document = SimpleNamespace(
        id=6,
        status={"state": "deleting"},
        updated_at=None,
        document_type=None,
        workspace_id=1,
        folder_id=None,
    )
    session = _FakeSession(document)

    class _Adapter:
        def __init__(self, _session):
            raise AssertionError("a deleting row must not be indexed")

    monkeypatch.setattr(url_tasks, "UrlDocumentAdapter", _Adapter)

    await url_tasks._index_markdown(
        session, document, "# text", "https://example.com/a", title="T", user_id="u"
    )

    assert document.status == {"state": "deleting"}
