"""``_parse_headers`` turns the one input it can reject into a ValueError."""

from __future__ import annotations

import pytest

from app.config import uvicorn as uvicorn_config

pytestmark = pytest.mark.unit


def test_parse_headers_splits_pairs() -> None:
    assert uvicorn_config._parse_headers("a:1,b:2") == [("a", "1"), ("b", "2")]


def test_parse_headers_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="Invalid headers format"):
        uvicorn_config._parse_headers(None)
