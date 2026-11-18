import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.db import Chunk, Document, DocumentStatus
from app.indexing_pipeline.document_persistence import (
    persist_scratch_index,
    rollback_and_persist_failure,
)

pytestmark = pytest.mark.unit


def _make_document(doc_id: int = 1) -> Document:
    document = MagicMock(spec=Document)
    document.id = doc_id
    document.content = None
    document.status = DocumentStatus.processing()
    return document


@pytest.mark.asyncio
async def test_persist_scratch_index_batches_commits(monkeypatch):
    monkeypatch.setattr(
        "app.indexing_pipeline.document_persistence.set_committed_value",
        lambda *_args, **_kwargs: None,
    )
    session = MagicMock()
    session.commit = AsyncMock()
    document = _make_document()
    chunks = [Chunk(content=f"c{i}", embedding=[0.1], position=i) for i in range(5)]
    perf = MagicMock()

    await persist_scratch_index(
        session,
        document,
        "body",
        chunks,
        batch_size=2,
        perf=perf,
    )

    assert session.commit.await_count == 5
    assert document.status == DocumentStatus.ready()


@pytest.mark.asyncio
async def test_persist_scratch_index_empty_chunks(monkeypatch):
    monkeypatch.setattr(
        "app.indexing_pipeline.document_persistence.set_committed_value",
        lambda *_args, **_kwargs: None,
    )
    session = MagicMock()
    session.commit = AsyncMock()
    document = _make_document()
    perf = MagicMock()

    await persist_scratch_index(
        session,
        document,
        "body",
        [],
        batch_size=200,
        perf=perf,
    )

    assert session.commit.await_count == 2
    assert document.status == DocumentStatus.ready()


# --------------------------------------------------------------------------
# rollback_and_persist_failure: a DB fault must not mask the original error
# --------------------------------------------------------------------------


class _FailingSession:
    """Every call fails with ``error``; ``calls`` records what was attempted."""

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.calls: list[str] = []

    async def rollback(self) -> None:
        self.calls.append("rollback")
        raise self._error

    async def refresh(self, _document) -> None:
        self.calls.append("refresh")

    async def commit(self) -> None:
        self.calls.append("commit")


@pytest.mark.asyncio
async def test_a_db_fault_while_marking_failed_is_swallowed(caplog):
    session = _FailingSession(OperationalError("SELECT 1", None, Exception("down")))
    document = _make_document()

    with caplog.at_level(
        logging.WARNING, logger="app.indexing_pipeline.document_persistence"
    ):
        await rollback_and_persist_failure(session, document, "nope")
    assert session.calls == ["rollback"]
    assert "Rollback failed" in caplog.text


@pytest.mark.asyncio
async def test_a_bug_while_marking_failed_is_not_swallowed():
    """Only the two DB shapes are contained; a bug keeps its traceback visible."""
    session = _FailingSession(RuntimeError("driver bug"))

    with pytest.raises(RuntimeError):
        await rollback_and_persist_failure(session, _make_document(), "nope")
