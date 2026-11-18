"""Batched MinerU conversion: batching, resume, failures, progress writes, lock."""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.config import config
from app.conversion import runner
from app.conversion.job_store import Job, JobStore
from app.conversion.mineru_client import (
    MinerUError,
    MinerUTaskFailed,
    MinerUTaskNotFound,
    MinerUUnavailable,
    TaskState,
)
from app.conversion.runner import StepOutcome, run_step, start_conversion
from app.tasks.celery_tasks.conversion_tasks import ConversionLock, lock_key

pytestmark = pytest.mark.unit

RESULT_URL = "http://mineru-api:8000/v1/tasks/t1/result"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDocument:
    def __init__(self, document_id: int = 1, state: str = "processing") -> None:
        self.id = document_id
        self.status = {"state": state}
        self.updated_at = None


class FakeSession:
    def __init__(self, document, maker: FakeSessionMaker) -> None:
        self._document = document
        self._maker = maker

    async def __aenter__(self):
        self._maker.active += 1
        return self

    async def __aexit__(self, *exc_info):
        self._maker.active -= 1
        return False

    async def get(self, _model, document_id):
        if self._document is not None and self._document.id == document_id:
            return self._document
        return None

    async def execute(self, *_args, **_kwargs):
        return None

    async def commit(self) -> None:
        if self._document is not None:
            self._maker.writes.append(dict(self._document.status))

    async def rollback(self) -> None:
        self._maker.rollbacks += 1


class FakeSessionMaker:
    """Mimics ``async_sessionmaker``: each ``()`` opens a tracked session."""

    def __init__(self, document) -> None:
        self.document = document
        self.writes: list[dict] = []
        self.active = 0
        self.opened = 0
        self.rollbacks = 0

    def __call__(self) -> FakeSession:
        self.opened += 1
        return FakeSession(self.document, self)


class FakeClient:
    """Scripted MinerU client: responses are queued per call."""

    def __init__(self) -> None:
        self.submits: list[tuple[str, str, str | None]] = []
        self.gets: list[str] = []
        self.cancels: list[str] = []
        self._submit_ids: list[str] = []
        self._get_script: list = []
        self._markdown_script: list[str] = []
        self.on_call = None

    def queue_submit(self, value) -> None:
        """Queue a task id, or an exception to raise from ``submit``."""
        self._submit_ids.append(value)

    def queue_get(self, value) -> None:
        self._get_script.append(value)

    def queue_markdown(self, markdown: str) -> None:
        self._markdown_script.append(markdown)

    def _note(self) -> None:
        if self.on_call is not None:
            self.on_call()

    async def submit(self, file_path, file_name, *, page_range, size=None, sha256=None):
        self._note()
        self.submits.append((Path(file_path).name, file_name, page_range))
        value = self._submit_ids.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def get(self, task_id: str) -> TaskState:
        self._note()
        self.gets.append(task_id)
        value = self._get_script.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def fetch_markdown(self, _result_url: str) -> str:
        self._note()
        value = self._markdown_script.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def cancel(self, task_id: str) -> None:
        self.cancels.append(task_id)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeLockRedis:
    """The subset of redis used by ConversionLock."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    def get(self, key):
        return self.kv.get(key)

    def expire(self, key, ttl):
        if key not in self.kv:
            return False
        self.expires[key] = ttl
        return True

    def delete(self, key):
        self.expires.pop(key, None)
        return 1 if self.kv.pop(key, None) is not None else 0

    def exists(self, key):
        return int(key in self.kv)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _store(tmp_path: Path, *, max_bytes: int = 1024**3) -> JobStore:
    return JobStore(tmp_path / "jobs", max_bytes)


def _pdf(tmp_path: Path, pages: int, *, name: str = "book.pdf", encrypt=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if encrypt is not None:
        writer.encrypt(encrypt)
    path = tmp_path / name
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def _owner_only_pdf(tmp_path: Path, pages: int, *, name: str = "ebook.pdf"):
    """An encrypted PDF with an empty user password (owner password only)."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    writer.encrypt(user_password="", owner_password="owner")
    path = tmp_path / name
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def _job(**overrides) -> Job:
    data = {
        "document_id": 1,
        "filename": "book.pdf",
        "workspace_id": 7,
        "user_id": "user-1",
        "use_vision_llm": False,
        "processing_mode": "basic",
        "source_name": "source.pdf",
        "source_sha256": "0" * 64,
        "pages_total": 40,
        "batch_pages": 10,
    }
    data.update(overrides)
    return Job(**data)


def _session(document):
    maker = FakeSessionMaker(document)
    return FakeSession(document, maker), maker


def _completed(task_id: str = "t1") -> TaskState:
    return TaskState(task_id=task_id, status="completed", result_url=RESULT_URL)


async def _start(
    tmp_path, *, source: Path, filename: str, store=None, client=None, document=None
):
    document = document or FakeDocument(1)
    session, maker = _session(document)
    store = store or _store(tmp_path)
    client = client or FakeClient()
    enqueued: list[tuple[int, float]] = []
    await start_conversion(
        session,
        document_id=document.id,
        temp_path=str(source),
        filename=filename,
        workspace_id=7,
        user_id="user-1",
        use_vision_llm=False,
        processing_mode="basic",
        store=store,
        client=client,
        enqueue=lambda document_id, delay: enqueued.append((document_id, delay)),
    )
    return document, store, client, enqueued, maker


async def _run(store, client, maker, *, clock=None, sleep=None, steps=1):
    results = []
    for _ in range(steps):
        results.append(
            await run_step(
                1,
                store=store,
                client=client,
                session_maker=lambda: maker,
                sleep=sleep or _no_sleep,
                clock=clock or time.time,
            )
        )
    return results


async def _no_sleep(_seconds: float) -> None:
    return None


def _record_indexing(monkeypatch) -> dict:
    recorded: dict = {}

    async def fake_process(
        document_id, temp_path, filename, workspace_id, user_id, **kwargs
    ):
        recorded.update(
            document_id=document_id,
            filename=filename,
            workspace_id=workspace_id,
            override=kwargs.get("markdown_override"),
            temp_existed=Path(temp_path).exists(),
        )

    monkeypatch.setattr(
        "app.tasks.celery_tasks.document_tasks._process_file_with_document",
        fake_process,
    )
    return recorded


# ---------------------------------------------------------------------------
# Batching and resume
# ---------------------------------------------------------------------------


async def test_pdf_25_pages_runs_three_batches_then_indexes(tmp_path, monkeypatch):
    source = _pdf(tmp_path, 25)
    client = FakeClient()
    for task_id in ("t1", "t2", "t3"):
        client.queue_submit(task_id)
        client.queue_get(_completed(task_id))
    for text in ("batch one", "batch two", "batch three"):
        client.queue_markdown(text)
    recorded = _record_indexing(monkeypatch)

    document, store, client, enqueued, maker = await _start(
        tmp_path, source=source, filename="book.pdf", client=client
    )

    assert enqueued == [(1, 0)]
    assert document.status["stage"] == "queued"
    assert document.status["pages_total"] == 25
    assert document.status["pages_done"] == 0

    for expected_pages_done in (10, 20, 25):
        ((outcome, _delay),) = await _run(store, client, maker)
        assert outcome is StepOutcome.CONTINUE
        assert document.status["pages_done"] == expected_pages_done

    ((outcome, _delay),) = await _run(store, client, maker)
    assert outcome is StepOutcome.DONE

    assert [submit[2] for submit in client.submits] == ["1-10", "11-20", "21-25"]
    assert recorded["override"] == "batch one\n\nbatch two\n\nbatch three"
    assert recorded["temp_existed"] is True
    assert store.list_ids() == []
    assert not (tmp_path / "jobs" / "1").exists()


async def test_resume_after_crash_continues_from_next_batch(tmp_path):
    store = _store(tmp_path)
    source = _pdf(tmp_path, 40)
    job = _job(next_batch=2, mineru_task_id="job_x")
    store.create(job, source)

    client = FakeClient()
    client.queue_get(_completed("job_x"))
    client.queue_markdown("resumed")
    maker = FakeSessionMaker(FakeDocument(1))

    ((outcome, _delay),) = await _run(store, client, maker)
    assert outcome is StepOutcome.CONTINUE
    assert client.submits == []  # polled the task saved in job.json
    assert client.gets == ["job_x"]
    assert store.load(1).next_batch == 3

    client.queue_submit("job_y")
    client.queue_get(_completed("job_y"))
    client.queue_markdown("next")
    ((outcome, _delay),) = await _run(store, client, maker)
    assert outcome is StepOutcome.CONTINUE
    assert client.submits == [("source.pdf", "source.pdf", "31-40")]


async def test_resubmits_when_mineru_lost_task(tmp_path):
    store = _store(tmp_path)
    source = _pdf(tmp_path, 20)
    store.create(_job(next_batch=0, mineru_task_id="job_x"), source)

    client = FakeClient()
    client.queue_get(MinerUTaskNotFound("task_not_found", "gone"))
    client.queue_submit("job_y")
    client.queue_get(_completed("job_y"))
    client.queue_markdown("recovered")

    ((outcome, _delay),) = await _run(store, client, FakeSessionMaker(FakeDocument(1)))
    assert outcome is StepOutcome.CONTINUE
    assert client.gets == ["job_x", "job_y"]
    assert client.submits == [("source.pdf", "source.pdf", "1-10")]
    assert store.load(1).next_batch == 1


# ---------------------------------------------------------------------------
# Per-batch deadline (a hung MinerU task must not run for hours)
# ---------------------------------------------------------------------------


def _advancing_clock(client: FakeClient, step: float = 1.0) -> FakeClock:
    clock = FakeClock()
    client.on_call = lambda: setattr(clock, "now", clock.now + step)
    return clock


async def test_batch_timeout_cancels_and_resubmits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MINERU_BATCH_TIMEOUT_SECONDS", 1.5)
    monkeypatch.setattr(config, "MINERU_POLL_SECONDS", 0.0)
    store = _store(tmp_path)
    store.create(_job(), _pdf(tmp_path, 10))

    client = FakeClient()
    clock = _advancing_clock(client)
    client.queue_submit("t1")
    client.queue_get(TaskState(task_id="t1", status="running"))
    client.queue_get(TaskState(task_id="t1", status="running"))
    client.queue_submit("t2")
    client.queue_get(_completed("t2"))
    client.queue_markdown("done")

    ((outcome, _delay),) = await _run(
        store, client, FakeSessionMaker(FakeDocument(1)), clock=clock
    )

    assert outcome is StepOutcome.CONTINUE
    assert client.cancels == ["t1"]
    assert [submit[2] for submit in client.submits] == ["1-10", "1-10"]
    job = store.load(1)
    assert job is not None
    assert job.next_batch == 1
    # A finished batch clears the deadline and the resubmit counter.
    assert job.batch_resubmits == 0
    assert job.batch_submitted_at is None


async def test_second_batch_timeout_fails_with_a_fixed_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MINERU_BATCH_TIMEOUT_SECONDS", 1.5)
    monkeypatch.setattr(config, "MINERU_POLL_SECONDS", 0.0)
    store = _store(tmp_path)
    store.create(_job(), _pdf(tmp_path, 10))
    document = FakeDocument(1)

    client = FakeClient()
    clock = _advancing_clock(client)
    client.queue_submit("t1")
    client.queue_get(TaskState(task_id="t1", status="running"))
    client.queue_get(TaskState(task_id="t1", status="running"))
    client.queue_submit("t2")
    client.queue_get(TaskState(task_id="t2", status="running"))
    client.queue_get(TaskState(task_id="t2", status="running"))

    ((outcome, _delay),) = await _run(
        store, client, FakeSessionMaker(document), clock=clock
    )

    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "failed"
    assert document.status["reason"] == runner.BATCH_TIMEOUT_REASON
    assert document.status["reason"] == "Converter stopped responding"
    assert client.cancels == ["t1", "t2"]  # both hung tasks are cancelled
    assert store.list_ids() == []


# ---------------------------------------------------------------------------
# MinerU failures
# ---------------------------------------------------------------------------


async def test_unavailable_backs_off_then_gives_up(tmp_path):
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 20), filename="book.pdf"
    )
    client.queue_submit("job_x")
    for _ in range(8):
        client.queue_get(MinerUUnavailable("unavailable", "down"))

    clock = FakeClock()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    for expected_delay in (60.0, 120.0, 240.0, 480.0, 600.0, 600.0):
        ((outcome, delay),) = await _run(
            store, client, maker, clock=clock, sleep=fake_sleep
        )
        assert outcome is StepOutcome.RETRY_LATER
        assert delay == expected_delay
        assert document.status["stage"] == "waiting_for_converter"
        # Every retry refreshes updated_at, so a live wait never reads stalled.
        assert document.status["updated_at"].endswith("Z")

    assert store.load(1).attempts == 6
    assert len(client.submits) == 1
    assert sleeps == []  # giving up on the converter never sleeps

    clock.now += 86401
    ((outcome, _delay),) = await _run(store, client, maker, clock=clock)
    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "failed"
    assert document.status["reason"].startswith("MinerU unreachable for 24 h")
    assert store.list_ids() == []


async def test_task_failed_sets_reason(tmp_path):
    store = _store(tmp_path)
    document = FakeDocument(1)
    _session_obj, maker = _session(document)
    store.create(_job(next_batch=0, mineru_task_id="job_x"), _pdf(tmp_path, 10))

    client = FakeClient()
    client.queue_get(MinerUTaskFailed("unsupported", "Unsupported file type: unknown"))

    ((outcome, _delay),) = await _run(store, client, maker)
    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "failed"
    assert document.status["reason"] == "MinerU: Unsupported file type: unknown"
    assert store.list_ids() == []


async def test_unreadable_pdf_fails_with_reason(tmp_path):
    random_bytes = tmp_path / "random.pdf"
    random_bytes.write_bytes(b"this is definitely not a pdf")
    encrypted = _pdf(tmp_path, 1, name="locked.pdf", encrypt="pw")

    for source in (random_bytes, encrypted):
        document, store, _client, enqueued, _maker = await _start(
            tmp_path, source=source, filename=source.name, store=_store(tmp_path)
        )
        assert document.status["state"] == "failed"
        assert document.status["reason"].startswith("Cannot read PDF")
        assert enqueued == []
        assert store.list_ids() == []


async def test_owner_password_only_pdf_is_read_and_converted(tmp_path):
    """Ebooks often carry only an owner password and open in every viewer."""
    source = _owner_only_pdf(tmp_path, 12)

    document, store, _client, enqueued, _maker = await _start(
        tmp_path, source=source, filename="ebook.pdf"
    )

    assert document.status["state"] == "processing"
    assert document.status["stage"] == "queued"
    assert document.status["pages_total"] == 12
    assert enqueued == [(1, 0)]
    job = store.load(1)
    assert job is not None and job.pages_total == 12


async def test_all_empty_batches_fail_clearly(tmp_path, monkeypatch):
    recorded = _record_indexing(monkeypatch)
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 20), filename="book.pdf"
    )
    for task_id in ("t1", "t2"):
        client.queue_submit(task_id)
        client.queue_get(_completed(task_id))
    client.queue_markdown("")
    client.queue_markdown("")

    await _run(store, client, maker, steps=2)
    ((outcome, _delay),) = await _run(store, client, maker)

    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "failed"
    assert document.status["reason"] == "MinerU found no text in this file"
    assert store.list_ids() == []
    assert recorded == {}


async def test_one_empty_batch_of_two_still_indexes(tmp_path, monkeypatch):
    recorded = _record_indexing(monkeypatch)
    _document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 20), filename="book.pdf"
    )
    for task_id in ("t1", "t2"):
        client.queue_submit(task_id)
        client.queue_get(_completed(task_id))
    client.queue_markdown("")
    client.queue_markdown("real text")

    await _run(store, client, maker, steps=2)
    ((outcome, _delay),) = await _run(store, client, maker)

    assert outcome is StepOutcome.DONE
    assert recorded["override"] == "\n\nreal text"
    assert store.list_ids() == []


# ---------------------------------------------------------------------------
# Document lifecycle
# ---------------------------------------------------------------------------


async def test_step_stops_when_document_deleted(tmp_path):
    store = _store(tmp_path)
    store.create(_job(next_batch=0, mineru_task_id="job_x"), _pdf(tmp_path, 10))
    client = FakeClient()

    ((outcome, _delay),) = await _run(store, client, FakeSessionMaker(None))

    assert outcome is StepOutcome.STOPPED
    assert client.cancels == ["job_x"]
    assert store.list_ids() == []


async def test_step_stops_and_cleans_dir_when_document_already_ready(tmp_path):
    store = _store(tmp_path)
    store.create(_job(), _pdf(tmp_path, 10))
    client = FakeClient()

    ((outcome, _delay),) = await _run(
        store, client, FakeSessionMaker(FakeDocument(1, state="ready"))
    )

    assert outcome is StepOutcome.STOPPED
    assert store.list_ids() == []


async def test_restart_replaces_existing_job(tmp_path):
    document = FakeDocument(1)
    session, _maker = _session(document)
    store = _store(tmp_path)
    client = FakeClient()

    async def start(source: Path):
        await start_conversion(
            session,
            document_id=1,
            temp_path=str(source),
            filename="book.pdf",
            workspace_id=7,
            user_id="user-1",
            use_vision_llm=False,
            processing_mode="basic",
            store=store,
            client=client,
            enqueue=lambda _i, _d: None,
        )

    await start(_pdf(tmp_path, 10, name="first.pdf"))
    job = store.load(1)
    job.mineru_task_id = "old_task"
    store.save(job)
    store.write_batch(job, 0, "stale batch")

    await start(_pdf(tmp_path, 10, name="second.pdf"))

    assert store.list_ids() == [1]
    reloaded = store.load(1)
    assert reloaded.next_batch == 0
    assert reloaded.mineru_task_id is None
    assert store.merged_markdown(reloaded) == ""
    assert client.cancels == ["old_task"]


async def test_heic_upload_becomes_png_source(tmp_path, monkeypatch):
    heic = tmp_path / "photo.heic"
    heic.write_bytes(b"pretend heic bytes")

    def fake_heic_to_png(src: Path, dst: Path) -> None:
        Path(dst).write_bytes(b"png bytes")

    monkeypatch.setattr(runner, "heic_to_png", fake_heic_to_png)

    _document, store, _client, enqueued, _maker = await _start(
        tmp_path, source=heic, filename="photo.heic"
    )

    job = store.load(1)
    assert job is not None
    assert job.source_name == "source.png"
    assert job.pages_total is None
    assert store.source_path(job).read_bytes() == b"png bytes"
    assert not (store._dir(1) / "source.heic").exists()
    assert enqueued == [(1, 0)]


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Cannot read HEIC image"),
        ImportError("No module named 'pi_heif'"),
    ],
)
async def test_heic_failure_fails_the_document(tmp_path, monkeypatch, error):
    """heic_to_png's own two error types still fail the document with its text."""
    heic = tmp_path / "photo.heic"
    heic.write_bytes(b"pretend heic bytes")

    def fake_heic_to_png(src: Path, dst: Path) -> None:
        raise error

    monkeypatch.setattr(runner, "heic_to_png", fake_heic_to_png)

    document, store, _client, enqueued, _maker = await _start(
        tmp_path, source=heic, filename="photo.heic"
    )

    assert document.status["state"] == "failed"
    assert document.status["reason"] == str(error)
    assert enqueued == []
    assert store.list_ids() == []


async def test_unexpected_heic_failure_reaches_the_start_guard(
    tmp_path, monkeypatch, caplog
):
    """A bug in the decoder is not caught here: the user gets the fixed reason
    and the traceback goes to the log, instead of a raw exception string."""
    heic = tmp_path / "photo.heic"
    heic.write_bytes(b"pretend heic bytes")

    def fake_heic_to_png(src: Path, dst: Path) -> None:
        raise RuntimeError("pillow exploded")

    monkeypatch.setattr(runner, "heic_to_png", fake_heic_to_png)

    with caplog.at_level(logging.ERROR, logger="app.conversion.runner"):
        document, store, _client, enqueued, _maker = await _start(
            tmp_path, source=heic, filename="photo.heic"
        )

    assert document.status["state"] == "failed"
    assert document.status["reason"] == runner.START_ERROR_REASON
    assert enqueued == []
    assert store.list_ids() == []
    records = [r for r in caplog.records if r.name == "app.conversion.runner"]
    assert records and all(r.exc_info is not None for r in records)


async def test_unreadable_pdf_logs_the_traceback(tmp_path, caplog):
    """The reason the user sees is fixed text; the log carries the traceback."""
    random_bytes = tmp_path / "random.pdf"
    random_bytes.write_bytes(b"this is definitely not a pdf")

    with caplog.at_level(logging.WARNING, logger="app.conversion.runner"):
        document, _store_obj, _client, enqueued, _maker = await _start(
            tmp_path, source=random_bytes, filename="random.pdf"
        )

    assert document.status["state"] == "failed"
    assert document.status["reason"].startswith("Cannot read PDF")
    assert enqueued == []
    records = [r for r in caplog.records if r.name == "app.conversion.runner"]
    assert records and all(r.exc_info is not None for r in records)


async def test_capacity_error_fails_document(tmp_path):
    store = _store(tmp_path, max_bytes=1)

    document, store, _client, enqueued, _maker = await _start(
        tmp_path, source=_pdf(tmp_path, 5), filename="book.pdf", store=store
    )

    assert document.status["state"] == "failed"
    assert (
        document.status["reason"]
        == "Conversion queue is full (disk limit); try again later"
    )
    assert enqueued == []
    assert store.list_ids() == []


async def test_non_pdf_is_single_batch_without_page_range(tmp_path):
    docx = tmp_path / "notes.docx"
    docx.write_bytes(b"pretend docx")
    document, store, client, enqueued, maker = await _start(
        tmp_path, source=docx, filename="notes.docx"
    )

    assert enqueued == [(1, 0)]
    assert "pages_total" not in document.status
    assert "pages_done" not in document.status

    client.queue_submit("t1")
    client.queue_get(_completed("t1"))
    client.queue_markdown("# notes")
    ((outcome, _delay),) = await _run(store, client, maker)

    assert outcome is StepOutcome.CONTINUE
    assert client.submits == [("source.docx", "source.docx", None)]
    assert "pages_total" not in document.status
    assert store.load(1).next_batch == 1


# ---------------------------------------------------------------------------
# Transaction discipline (keep-alive must never block on the runner)
# ---------------------------------------------------------------------------


async def test_no_session_is_held_across_mineru_http_calls(tmp_path, monkeypatch):
    _record_indexing(monkeypatch)
    _document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    client.queue_submit("t1")
    client.queue_get(_completed("t1"))
    client.queue_markdown("done")

    active_during_calls: list[int] = []
    client.on_call = lambda: active_during_calls.append(maker.active)

    await _run(store, client, maker, steps=2)

    # Every HTTP call happened with zero sessions open: status writes commit
    # in their own short transactions, never around MinerU I/O.
    assert active_during_calls
    assert set(active_during_calls) == {0}
    assert maker.opened > 3


# ---------------------------------------------------------------------------
# Redis lock
# ---------------------------------------------------------------------------


def test_lock_is_exclusive_and_owner_checked():
    redis_client = FakeLockRedis()
    first = ConversionLock(redis_client, 1)
    second = ConversionLock(redis_client, 1)

    assert first.acquire() is True
    assert second.acquire() is False
    assert first.refresh() is True

    # Simulate expiry + another worker taking the lock.
    redis_client.kv[lock_key(1)] = second.token
    assert first.release() is False
    assert redis_client.get(lock_key(1)) == second.token

    assert second.release() is True
    assert lock_key(1) not in redis_client.kv


def test_lock_refresher_keeps_key_alive_then_release_deletes():
    redis_client = FakeLockRedis()
    lock = ConversionLock(redis_client, 2, ttl=300, refresh=0.01)
    assert lock.acquire() is True
    lock.start_refresher()
    time.sleep(0.05)

    assert redis_client.get(lock_key(2)) == lock.token
    assert redis_client.expires[lock_key(2)] == 300

    assert lock.release() is True
    assert lock_key(2) not in redis_client.kv


# ---------------------------------------------------------------------------
# Upload hand-off condition
# ---------------------------------------------------------------------------


def test_mineru_handoff_applies_except_for_vision_llm_images(monkeypatch):
    from app.config import config
    from app.tasks.celery_tasks.document_tasks import _mineru_handoff_applies

    monkeypatch.setattr(config, "ETL_SERVICE", "MINERU")
    assert _mineru_handoff_applies("book.pdf", use_vision_llm=False) is True
    assert _mineru_handoff_applies("book.pdf", use_vision_llm=True) is True
    assert _mineru_handoff_applies("photo.heic", use_vision_llm=False) is True
    assert _mineru_handoff_applies("photo.HEIC", use_vision_llm=True) is False
    assert _mineru_handoff_applies("scan.tif", use_vision_llm=True) is False
    assert _mineru_handoff_applies("notes.txt", use_vision_llm=False) is False

    monkeypatch.setattr(config, "ETL_SERVICE", "DOCLING")
    assert _mineru_handoff_applies("book.pdf", use_vision_llm=False) is False


async def test_inflight_step_ignores_a_replaced_job(tmp_path, monkeypatch):
    """A re-upload during a poll must not write into the new job (Review Focus 1)."""
    source = _pdf(tmp_path, 10, name="first.pdf")
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=source, filename="book.pdf"
    )
    client.queue_submit("t1")
    original = store.load(1)
    assert original is not None

    async def replace_then_return_old_markdown(_result_url: str) -> str:
        replacement = _job()
        replacement.created_at = original.created_at + 1
        # A second upload landed mid-step with its own temp file.
        store.create(replacement, _pdf(tmp_path, 10, name="second.pdf"))
        return "stale batch"

    client.fetch_markdown = replace_then_return_old_markdown
    client.queue_get(_completed("t1"))

    ((outcome, _delay),) = await _run(store, client, maker)

    assert outcome is StepOutcome.CONTINUE
    assert store.load(1).created_at == original.created_at + 1
    assert store.load(1).next_batch == 0
    assert not list((tmp_path / "jobs" / "1").glob("batch-*.md"))
    assert document.status["state"] == "processing"


# ---------------------------------------------------------------------------
# MinerU response errors (T2 review): never a silent success
# ---------------------------------------------------------------------------


async def test_invalid_response_fails_document_with_reason(tmp_path):
    """A 2xx body that is not the expected JSON fails the document, loudly."""
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    client.queue_submit(
        MinerUError("invalid_response", "MinerU create-task response is not JSON")
    )

    ((outcome, _delay),) = await _run(store, client, maker)

    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "failed"
    assert (
        document.status["reason"] == "MinerU: MinerU create-task response is not JSON"
    )
    assert store.list_ids() == []


async def test_rate_limited_waits_instead_of_failing(tmp_path):
    """429/408 arrive as MinerUUnavailable, so the document waits (T2 M1)."""
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    client.queue_submit("t1")
    client.queue_get(MinerUUnavailable("rate_limited", "converter busy"))

    ((outcome, delay),) = await _run(store, client, maker)

    assert outcome is StepOutcome.RETRY_LATER
    assert delay == 60.0
    assert document.status["state"] == "processing"
    assert document.status["stage"] == "waiting_for_converter"
    assert store.load(1).attempts == 1


# ---------------------------------------------------------------------------
# Corrupt / vanished job state (T5 review): never crash the worker
# ---------------------------------------------------------------------------


async def test_corrupt_job_fails_document_and_cleans_up(tmp_path):
    document, store, _client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    (store._dir(1) / "job.json").write_text("{broken")

    ((outcome, _delay),) = await _run(store, _client, maker)

    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "failed"
    assert document.status["reason"] == runner.CORRUPT_REASON
    assert store.list_ids() == []


async def test_stale_corrupt_step_leaves_a_finished_document_alone(tmp_path):
    """A queued step that finds corrupt state must not overwrite READY/FAILED."""
    document, store, _client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    (store._dir(1) / "job.json").write_text("{broken")
    document.status = {"state": "ready"}

    ((outcome, _delay),) = await _run(store, _client, maker)

    assert outcome is StepOutcome.STOPPED
    assert document.status == {"state": "ready"}
    assert store.list_ids() == []  # the unusable state is still dropped


async def test_job_gone_mid_step_stops_without_failing_document(tmp_path):
    """A directory removed mid-step means "document deleted": stop quietly."""
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    client.queue_submit("t1")
    job_dir = store._dir(1)
    client.on_call = lambda: shutil.rmtree(job_dir, ignore_errors=True)

    ((outcome, _delay),) = await _run(store, client, maker)

    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "processing"
    assert not job_dir.exists()


async def test_reupload_replaces_corrupt_state(tmp_path):
    document = FakeDocument(1)
    session, _maker = _session(document)
    store = _store(tmp_path)
    client = FakeClient()

    async def start(source: Path):
        await start_conversion(
            session,
            document_id=1,
            temp_path=str(source),
            filename="book.pdf",
            workspace_id=7,
            user_id="user-1",
            use_vision_llm=False,
            processing_mode="basic",
            store=store,
            client=client,
            enqueue=lambda _i, _d: None,
        )

    await start(_pdf(tmp_path, 10, name="first.pdf"))
    (store._dir(1) / "job.json").write_text("{broken")

    await start(_pdf(tmp_path, 10, name="second.pdf"))

    reloaded = store.load(1)
    assert reloaded is not None
    assert reloaded.next_batch == 0
    assert document.status["state"] == "processing"


async def test_resume_sweep_fails_corrupt_job_and_drops_orphans(tmp_path):
    from app.tasks.celery_tasks.conversion_tasks import _resume_conversions

    document = FakeDocument(1)
    maker = FakeSessionMaker(document)
    store = _store(tmp_path)
    store.create(_job(), _pdf(tmp_path, 10))
    (store._dir(1) / "job.json").write_text("{broken")

    orphan = tmp_path / "jobs" / "2"
    orphan.mkdir(parents=True)
    staging = tmp_path / "jobs" / "3.new"
    staging.mkdir()
    old = time.time() - 3600
    os.utime(staging, (old, old))

    enqueued: list[int] = []
    await _resume_conversions(
        store=store,
        redis_client=FakeLockRedis(),
        session_maker=lambda: maker,
        enqueue=enqueued.append,
    )

    assert enqueued == []
    assert store.list_ids() == []
    assert not orphan.exists()
    assert not staging.exists()
    assert document.status["state"] == "failed"
    assert document.status["reason"] == runner.CORRUPT_REASON


async def test_resume_sweep_skips_a_document_being_deleted(tmp_path):
    """The sweep must never flip a deleting document back to a live state."""
    from app.tasks.celery_tasks.conversion_tasks import _resume_conversions

    document = FakeDocument(1, state="deleting")
    maker = FakeSessionMaker(document)
    store = _store(tmp_path)
    store.create(_job(), _pdf(tmp_path, 10))

    enqueued: list[int] = []
    await _resume_conversions(
        store=store,
        redis_client=FakeLockRedis(),
        session_maker=lambda: maker,
        enqueue=enqueued.append,
    )

    assert enqueued == []
    assert document.status == {"state": "deleting"}
    assert store.list_ids() == [1]  # the delete route owns the job directory


async def test_write_status_never_overwrites_a_deleting_document(tmp_path):
    document = FakeDocument(1, state="deleting")
    maker = FakeSessionMaker(document)

    wrote = await runner.write_status(lambda: maker, 1, {"state": "processing"})

    assert wrote is False
    assert document.status == {"state": "deleting"}


# ---------------------------------------------------------------------------
# Sweep scheduling + status preservation (T6 I1/I2)
# ---------------------------------------------------------------------------


async def test_sweep_skips_a_live_schedule_marker_and_resumes_after_expiry(tmp_path):
    from app.tasks.celery_tasks.conversion_tasks import (
        _resume_conversions,
        schedule_key,
    )

    document = FakeDocument(1)
    maker = FakeSessionMaker(document)
    store = _store(tmp_path)
    store.create(_job(), _pdf(tmp_path, 10))

    redis_client = FakeLockRedis()
    redis_client.set(schedule_key(1), "1", ex=120)
    enqueued: list[int] = []

    async def sweep() -> None:
        await _resume_conversions(
            store=store,
            redis_client=redis_client,
            session_maker=lambda: maker,
            enqueue=enqueued.append,
        )

    await sweep()
    assert enqueued == []  # a successor is already queued
    assert store.list_ids() == [1]

    redis_client.delete(schedule_key(1))  # the marker expired
    await sweep()
    assert enqueued == [1]
    assert document.status["stage"] == "queued"


async def test_sweep_keeps_waiting_for_converter_stage(tmp_path):
    from app.tasks.celery_tasks.conversion_tasks import _resume_conversions

    document = FakeDocument(1)
    document.status = {"state": "processing", "stage": "waiting_for_converter"}
    maker = FakeSessionMaker(document)
    store = _store(tmp_path)
    store.create(_job(), _pdf(tmp_path, 10))

    enqueued: list[int] = []
    await _resume_conversions(
        store=store,
        redis_client=FakeLockRedis(),
        session_maker=lambda: maker,
        enqueue=enqueued.append,
    )

    assert enqueued == [1]
    assert document.status["stage"] == "waiting_for_converter"
    assert maker.writes == []  # the sweep never downgraded the stage


async def test_sweep_stamps_queued_only_when_it_enqueues(tmp_path):
    from app.tasks.celery_tasks.conversion_tasks import _resume_conversions

    document = FakeDocument(1)
    document.status = {"state": "processing", "stage": "converting"}
    maker = FakeSessionMaker(document)
    store = _store(tmp_path)
    store.create(_job(), _pdf(tmp_path, 10))

    enqueued: list[int] = []
    await _resume_conversions(
        store=store,
        redis_client=FakeLockRedis(),
        session_maker=lambda: maker,
        enqueue=enqueued.append,
    )

    assert enqueued == [1]
    assert document.status["stage"] == "queued"


def test_mark_scheduled_sets_ttl_delay_plus_grace():
    from app.tasks.celery_tasks.conversion_tasks import (
        SCHEDULE_GRACE_SECONDS,
        mark_scheduled,
        schedule_key,
    )

    redis_client = FakeLockRedis()
    mark_scheduled(redis_client, 7, 0.0)
    assert redis_client.expires[schedule_key(7)] == SCHEDULE_GRACE_SECONDS

    mark_scheduled(redis_client, 7, 600.0)
    assert redis_client.expires[schedule_key(7)] == 600 + SCHEDULE_GRACE_SECONDS


# ---------------------------------------------------------------------------
# Step wrapper: lock retry + marker lifecycle (T6 I1/M6)
# ---------------------------------------------------------------------------


def _run_coroutine(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_step_clears_its_marker_and_reenqueues_with_one(monkeypatch):
    from app.tasks.celery_tasks import conversion_tasks as ct

    redis_client = FakeLockRedis()
    redis_client.set(ct.schedule_key(1), "1", ex=120)
    monkeypatch.setattr(ct, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(ct, "run_async_celery_task", lambda fn: _run_coroutine(fn()))

    async def fake_step(document_id, **_kwargs):
        # The marker is already gone: this step owns the document now.
        assert ct.schedule_key(document_id) not in redis_client.kv
        return StepOutcome.CONTINUE, 0.0

    monkeypatch.setattr("app.conversion.runner.run_step", fake_step)
    enqueued: list[tuple[int, float]] = []
    monkeypatch.setattr(
        ct.convert_document_step,
        "apply_async",
        lambda args, countdown=0, **_kwargs: enqueued.append((args[0], countdown)),
    )

    ct.convert_document_step.run(1)

    assert enqueued == [(1, 0.0)]
    assert redis_client.expires[ct.schedule_key(1)] == 120
    assert ct.lock_key(1) not in redis_client.kv  # lock released after the step


def test_step_retries_when_another_worker_holds_the_lock(monkeypatch):
    from celery.exceptions import Retry

    from app.tasks.celery_tasks import conversion_tasks as ct

    redis_client = FakeLockRedis()
    redis_client.set(ct.lock_key(1), "someone-else", nx=True, ex=300)
    monkeypatch.setattr(ct, "get_redis_client", lambda: redis_client)

    with pytest.raises(Retry):
        ct.convert_document_step.run(1)


def test_step_gives_up_cleanly_after_max_lock_retries(monkeypatch):
    """The task's retry bound matches the guard, so the last pass returns (fix 3)."""
    from celery.exceptions import Retry

    from app.tasks.celery_tasks import conversion_tasks as ct

    assert ct.convert_document_step.max_retries == ct.MAX_LOCK_RETRIES
    redis_client = FakeLockRedis()
    redis_client.set(ct.lock_key(1), "someone-else", nx=True, ex=300)
    monkeypatch.setattr(ct, "get_redis_client", lambda: redis_client)

    for retries in range(ct.MAX_LOCK_RETRIES):
        ct.convert_document_step.push_request(retries=retries, id=f"r{retries}")
        try:
            with pytest.raises(Retry):
                ct.convert_document_step.run(1)
        finally:
            ct.convert_document_step.pop_request()

    # Celery would raise MaxRetriesExceededError if retry() were called here;
    # the guard returns instead, leaving recovery to the sweep.
    ct.convert_document_step.push_request(retries=ct.MAX_LOCK_RETRIES, id="last")
    try:
        assert ct.convert_document_step.run(1) is None
    finally:
        ct.convert_document_step.pop_request()


# ---------------------------------------------------------------------------
# Supersession during polling (T6 M1/M2)
# ---------------------------------------------------------------------------


async def test_superseded_during_poll_keeps_the_replacement_moving(tmp_path):
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    client.queue_submit("t1")
    original = store.load(1)
    assert original is not None

    async def fail_after_replacement(_task_id):
        replacement = _job()
        replacement.created_at = original.created_at + 1
        store.create(replacement, _pdf(tmp_path, 10, name="second.pdf"))
        raise MinerUTaskFailed("cancelled", "canceled")

    client.get = fail_after_replacement

    ((outcome, _delay),) = await _run(store, client, maker)

    assert outcome is StepOutcome.CONTINUE  # the live replacement needs a step
    assert document.status["state"] == "processing"
    assert store.load(1).created_at == original.created_at + 1
    assert store.load(1).next_batch == 0


async def test_poll_loop_stops_a_superseded_step(tmp_path):
    """The inner poll loop notices the replacement before touching its state."""
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    client.queue_submit("t1")
    original = store.load(1)
    assert original is not None
    polls = {"count": 0}

    async def replace_on_second_poll(task_id):
        polls["count"] += 1
        if polls["count"] == 1:
            return TaskState(task_id=task_id, status="running")
        replacement = _job()
        replacement.created_at = original.created_at + 1
        store.create(replacement, _pdf(tmp_path, 10, name="second.pdf"))
        return TaskState(task_id=task_id, status="running")

    client.get = replace_on_second_poll

    ((outcome, _delay),) = await _run(store, client, maker, sleep=_no_sleep)

    assert outcome is StepOutcome.CONTINUE
    assert store.load(1).created_at == original.created_at + 1
    assert not list((tmp_path / "jobs" / "1").glob("batch-*.md"))
    assert document.status["state"] == "processing"


# ---------------------------------------------------------------------------
# 24 h clock across a blip (T6 M3) and unexpected step errors (T6 M4)
# ---------------------------------------------------------------------------


async def test_successful_poll_restarts_the_24h_clock(tmp_path):
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 20), filename="book.pdf"
    )
    clock = FakeClock()

    # First attempt: MinerU is down for one step.
    client.queue_submit("t1")
    client.queue_get(MinerUUnavailable("unavailable", "down"))
    ((outcome, _delay),) = await _run(store, client, maker, clock=clock)
    assert outcome is StepOutcome.RETRY_LATER
    assert store.load(1).attempts == 1
    first_down = store.load(1).unreachable_since

    # Recovery: the saved task completes; the 24 h clock must reset.
    client.queue_get(_completed("t1"))
    client.queue_markdown("batch one")
    clock.now += 90_000  # well past 24 h since the first blip
    ((outcome, _delay),) = await _run(store, client, maker, clock=clock)
    assert outcome is StepOutcome.CONTINUE
    recovered = store.load(1)
    assert recovered.unreachable_since is None
    assert recovered.attempts == 0

    # A new blip starts its own 24 h clock instead of inheriting the old one.
    client.queue_submit("t2")
    client.queue_get(MinerUUnavailable("unavailable", "down again"))
    ((outcome, delay),) = await _run(store, client, maker, clock=clock)
    assert outcome is StepOutcome.RETRY_LATER
    assert delay == 60.0
    assert store.load(1).unreachable_since == clock.now
    assert first_down is not None
    assert document.status["state"] == "processing"


async def test_unexpected_errors_back_off_then_fail_with_a_fixed_reason(tmp_path):
    document, store, client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    for _ in range(5):
        client.queue_submit("t1")
        client.queue_get(_completed("t1"))
        client.queue_markdown(OSError("disk full at /secret/path key=abc"))

    delays: list[float] = []
    for expected in (60.0, 120.0, 240.0, 480.0):
        ((outcome, delay),) = await _run(store, client, maker)
        assert outcome is StepOutcome.RETRY_LATER
        assert delay == expected
        delays.append(delay)
        assert document.status["state"] == "processing"
    assert delays == [60.0, 120.0, 240.0, 480.0]
    assert store.load(1).step_errors == 4

    ((outcome, _delay),) = await _run(store, client, maker)
    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "failed"
    assert document.status["reason"] == runner.STEP_ERROR_REASON
    assert "secret" not in document.status["reason"]
    assert "disk" not in document.status["reason"]
    assert store.list_ids() == []


# ---------------------------------------------------------------------------
# Early failure cleanup (T6 M5)
# ---------------------------------------------------------------------------


async def test_early_pdf_failure_cleans_a_previous_job_and_the_temp_file(tmp_path):
    store = _store(tmp_path)
    client = FakeClient()
    previous = _job(next_batch=2, mineru_task_id="old_task")
    store.create(previous, _pdf(tmp_path, 10, name="old.pdf"))

    bad_pdf = tmp_path / "broken.pdf"
    bad_pdf.write_bytes(b"definitely not a pdf")

    document, store, client, enqueued, _maker = await _start(
        tmp_path, source=bad_pdf, filename="broken.pdf", store=store, client=client
    )

    assert document.status["state"] == "failed"
    assert document.status["reason"].startswith("Cannot read PDF")
    assert client.cancels == ["old_task"]
    assert store.list_ids() == []
    assert enqueued == []
    assert not bad_pdf.exists()


async def test_unexpected_start_error_fails_document_and_cleans_up(
    tmp_path, monkeypatch
):
    """An unforeseen start failure must never leave a pending ghost behind."""
    store = _store(tmp_path)
    source = _pdf(tmp_path, 5)
    document = FakeDocument(1)
    session, maker = _session(document)
    staging = tmp_path / "jobs" / "1.new"
    staging.mkdir(parents=True)
    job_dir = tmp_path / "jobs" / "1"
    job_dir.mkdir()

    def boom(*_args, **_kwargs):
        raise RuntimeError("unexpected staging failure /app/conversions/1/source.pdf")

    monkeypatch.setattr(store, "create", boom)

    # No exception escapes: the Celery task must not crash-loop.
    await start_conversion(
        session,
        document_id=1,
        temp_path=str(source),
        filename="book.pdf",
        workspace_id=7,
        user_id="user-1",
        use_vision_llm=False,
        processing_mode="basic",
        store=store,
        client=FakeClient(),
        enqueue=lambda _i, _d: None,
    )

    assert document.status["state"] == "failed"
    assert document.status["reason"] == runner.START_ERROR_REASON
    assert document.status["reason"] == "Could not start conversion"
    # A DB error leaves the session needing a rollback before the failed mark commits.
    assert maker.rollbacks == 1
    assert not source.exists()
    assert store.list_ids() == []
    assert not staging.exists()
    assert not job_dir.exists()


async def test_enqueue_failure_fails_document_and_cleans_up(tmp_path):
    """A broker/Redis failure on the first enqueue must not leave a pending row."""
    store = _store(tmp_path)
    source = _pdf(tmp_path, 5)
    document = FakeDocument(1)
    session, _maker = _session(document)

    def boom(_document_id, _delay):
        raise RuntimeError("redis down")

    await start_conversion(
        session,
        document_id=1,
        temp_path=str(source),
        filename="book.pdf",
        workspace_id=7,
        user_id="user-1",
        use_vision_llm=False,
        processing_mode="basic",
        store=store,
        client=FakeClient(),
        enqueue=boom,
    )

    assert document.status["state"] == "failed"
    assert document.status["reason"] == runner.START_ERROR_REASON
    assert not source.exists()
    assert store.list_ids() == []


# ---------------------------------------------------------------------------
# Setup failures are counted like step failures (fix 3)
# ---------------------------------------------------------------------------


async def test_client_setup_error_is_counted_and_backs_off(tmp_path, monkeypatch):
    document, store, _client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )

    def boom() -> None:
        # A malformed MINERU_URL is what the client's origin check raises.
        raise ValueError("bad MINERU_URL /secret/path key=abc")

    monkeypatch.setattr(runner, "client_from_config", boom)

    async def step():
        return await run_step(
            1,
            store=store,
            session_maker=lambda: maker,
            sleep=_no_sleep,
        )

    for expected in (60.0, 120.0, 240.0, 480.0):
        outcome, delay = await step()
        assert outcome is StepOutcome.RETRY_LATER
        assert delay == expected
    assert store.load(1).step_errors == 4
    assert document.status["state"] == "processing"

    outcome, _delay = await step()
    assert outcome is StepOutcome.STOPPED
    assert document.status["state"] == "failed"
    assert document.status["reason"] == runner.STEP_ERROR_REASON
    assert "secret" not in document.status["reason"]
    assert store.list_ids() == []


async def test_setup_error_outside_the_counted_set_is_not_counted(
    tmp_path, monkeypatch
):
    """The setup handler only covers the two ways client/state can fail."""
    _document, store, _client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )

    def boom() -> None:
        raise RuntimeError("a bug in our own code")

    monkeypatch.setattr(runner, "client_from_config", boom)

    with pytest.raises(RuntimeError):
        await run_step(1, store=store, session_maker=lambda: maker, sleep=_no_sleep)


async def test_state_read_error_is_counted_and_retried(tmp_path, monkeypatch):
    document, store, _client, _enqueued, maker = await _start(
        tmp_path, source=_pdf(tmp_path, 10), filename="book.pdf"
    )
    real_load = store.load
    calls = {"n": 0}

    def flaky(document_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("state disk hiccup")
        return real_load(document_id)

    monkeypatch.setattr(store, "load", flaky)

    ((outcome, delay),) = await _run(store, _client, maker)

    assert outcome is StepOutcome.RETRY_LATER
    assert delay == 60.0
    assert real_load(1).step_errors == 1
    assert document.status["state"] == "processing"


# ---------------------------------------------------------------------------
# Finishing never deletes a replacement's job, and stale hand-offs are reaped
# ---------------------------------------------------------------------------


async def test_finish_leaves_a_replacement_job_alone(tmp_path, monkeypatch):
    """Indexing can outlive a re-upload; the finish must not delete that job."""
    store = _store(tmp_path)
    document = FakeDocument(1)
    _session_obj, maker = _session(document)
    original = _job(created_at=100.0, pages_total=10, batch_pages=10, next_batch=1)
    store.create(original, _pdf(tmp_path, 10))
    store.write_batch(original, 0, "done")
    replacement = _job(created_at=200.0)
    new_source = _pdf(tmp_path, 5, name="new.pdf")

    async def fake_process(*_args, **_kwargs):
        store.create(replacement, new_source)

    monkeypatch.setattr(
        "app.tasks.celery_tasks.document_tasks._process_file_with_document",
        fake_process,
    )

    ((outcome, _delay),) = await _run(store, FakeClient(), maker)

    assert outcome is StepOutcome.DONE
    assert store.list_ids() == [1]
    assert store.load(1).created_at == 200.0


def test_stale_handoffs_are_reaped_from_the_shared_temp_dir(tmp_path):
    from app.tasks.celery_tasks.conversion_tasks import remove_stale_handoffs

    old = tmp_path / "mineru-old-handoff"
    fresh = tmp_path / "mineru-new-handoff"
    unrelated = tmp_path / "upload-notes.md"
    for path in (old, fresh, unrelated):
        path.write_text("x")
    now = time.time()
    for path in (old, unrelated):
        os.utime(path, (now - 25 * 3600, now - 25 * 3600))

    removed = remove_stale_handoffs(str(tmp_path), now=now)

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    assert unrelated.exists()
