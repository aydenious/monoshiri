import logging

import pytest

from app.db_schema import check_embedding_dimension_schema

pytestmark = pytest.mark.unit


def test_matching_dimensions_pass() -> None:
    check_embedding_dimension_schema(
        {"chunks.embedding": 2000, "documents.embedding": 2000}, 2000
    )


def test_one_mismatched_table_raises_with_both_dimensions() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        check_embedding_dimension_schema(
            {"chunks.embedding": 1536, "documents.embedding": 2000}, 2000
        )

    message = str(excinfo.value)
    assert "chunks.embedding" in message
    assert "1536" in message
    assert "2000" in message


def test_mismatch_is_logged_at_error_before_raising(caplog) -> None:
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as excinfo:
        check_embedding_dimension_schema({"chunks.embedding": 1536}, 2000)

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert str(excinfo.value) in errors


def test_missing_table_is_ignored() -> None:
    check_embedding_dimension_schema({"chunks.embedding": 2000}, 2000)
