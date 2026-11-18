"""``web.crawl`` I/O contract: camelCase surface, bounds, and billing counters."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.capabilities.web.crawl.schemas import (
    CrawlInput,
)

pytestmark = pytest.mark.unit


def test_requires_at_least_one_start_url() -> None:
    with pytest.raises(ValidationError):
        CrawlInput(startUrls=[])


def test_rejects_malformed_start_url() -> None:
    with pytest.raises(ValidationError):
        CrawlInput(startUrls=["not-a-url"])


def test_camelcase_fields_and_defaults() -> None:
    model = CrawlInput(startUrls=["https://e.com"])
    assert model.startUrls == ["https://e.com"]
    assert model.maxCrawlDepth == 0
    assert model.maxCrawlPages == 10
    assert model.maxLength == 50_000


def test_depth_and_page_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        CrawlInput(startUrls=["https://e.com"], maxCrawlDepth=-1)
    with pytest.raises(ValidationError):
        CrawlInput(startUrls=["https://e.com"], maxCrawlDepth=99)
    with pytest.raises(ValidationError):
        CrawlInput(startUrls=["https://e.com"], maxCrawlPages=0)
