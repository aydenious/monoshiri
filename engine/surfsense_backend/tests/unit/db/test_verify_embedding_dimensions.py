"""Unit tests for the read-only pgvector dimension probe in ``app.db``.

No database: a fake async connection records every statement and parameter
and returns scripted ``atttypmod`` rows.
"""

import logging

import pytest

import app.db as db_module
from app.db import embedding_columns, verify_embedding_dimensions

pytestmark = pytest.mark.unit

_QUERY_ATTRTYP_MOD = "SELECT atttypmod FROM pg_attribute"


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeConnection:
    """Scripted ``(table, column) -> row`` responses, recording every call."""

    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return _FakeResult(self.rows.get((params["table"], params["column"])))


class _FakeConnect:
    def __init__(self, conn):
        self.conn = conn

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakeEmbedder:
    def __init__(self, dimension):
        self.dimension = dimension


class _FakeConfig:
    def __init__(self, dimension):
        self.embedding_model_instance = _FakeEmbedder(dimension)


def _rows_all(dimension):
    return dict.fromkeys(embedding_columns(), (dimension,))


@pytest.fixture
def capture_check(monkeypatch):
    """Replace the comparison with a recorder and pin the configured dim."""
    calls = []

    def fake_check(db_dimensions, configured_dimension):
        calls.append((dict(db_dimensions), configured_dimension))

    monkeypatch.setattr(db_module, "check_embedding_dimension_schema", fake_check)
    monkeypatch.setattr(db_module, "config", _FakeConfig(2000))
    return calls


async def test_queries_every_vector_column_with_table_and_column_params(capture_check):
    conn = _FakeConnection(_rows_all(2000))

    await verify_embedding_dimensions(connect=_FakeConnect(conn))

    columns = embedding_columns()
    assert columns  # the metadata really has Vector columns
    assert len(conn.executed) == len(columns)
    for statement, params in conn.executed:
        assert _QUERY_ATTRTYP_MOD in statement
        assert "attname = :column" in statement
        # Bound parameters, never interpolated into the SQL string.
        assert "embedding" not in statement
        assert (params["table"], params["column"]) in columns
    assert {(p["table"], p["column"]) for _, p in conn.executed} == set(columns)


async def test_missing_table_is_absent_from_mapping(capture_check):
    rows = _rows_all(2000)
    rows[("documents", "embedding")] = None  # to_regclass found nothing
    conn = _FakeConnection(rows)

    await verify_embedding_dimensions(connect=_FakeConnect(conn))

    db_dimensions, configured = capture_check[0]
    assert configured == 2000
    assert db_dimensions == {"chunks.embedding": 2000}


async def test_mismatch_raises_runtime_error(monkeypatch):
    monkeypatch.setattr(db_module, "config", _FakeConfig(2000))
    rows = _rows_all(2000)
    rows[("chunks", "embedding")] = (1536,)
    conn = _FakeConnection(rows)

    with pytest.raises(RuntimeError) as excinfo:
        await verify_embedding_dimensions(connect=_FakeConnect(conn))

    message = str(excinfo.value)
    assert "chunks.embedding" in message
    assert "1536" in message
    assert "2000" in message


@pytest.mark.parametrize("typmod", [-1, 0])
async def test_unsized_column_is_skipped_with_warning(typmod, capture_check, caplog):
    rows = _rows_all(2000)
    rows[("chunks", "embedding")] = (typmod,)
    conn = _FakeConnection(rows)

    with caplog.at_level(logging.WARNING):
        await verify_embedding_dimensions(connect=_FakeConnect(conn))

    db_dimensions, _ = capture_check[0]
    assert "chunks.embedding" not in db_dimensions
    warnings = [
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any(
        "chunks.embedding" in warning
        and "has no fixed dimension; not checked" in warning
        for warning in warnings
    )
