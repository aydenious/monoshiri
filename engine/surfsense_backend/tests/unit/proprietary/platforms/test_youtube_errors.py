"""YouTube error-handling sites: narrowed subtitle fallback, logged fan-out."""

import logging

import pytest
from youtube_transcript_api import NoTranscriptFound

from app.proprietary.platforms.youtube import scraper, subtitles

pytestmark = pytest.mark.unit


class _TranscriptList:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def find_generated_transcript(self, _codes):
        raise self._exc

    def find_transcript(self, _codes):
        return "fallback"


def test_no_generated_transcript_falls_back() -> None:
    exc = NoTranscriptFound("vid", ["en"], None)
    assert subtitles._select_transcript(_TranscriptList(exc), "en", True) == "fallback"


def test_other_error_in_generated_lookup_propagates() -> None:
    with pytest.raises(KeyError):
        subtitles._select_transcript(_TranscriptList(KeyError("bug")), "en", True)


async def test_fan_out_logs_traceback_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def bad():
        raise ValueError("parser broke")
        yield {}

    async def good():
        yield {"ok": 1}

    with caplog.at_level(logging.WARNING, logger=scraper.logger.name):
        items = [i async for i in scraper.fan_out([bad(), good()], concurrency=1)]
    assert items == [{"ok": 1}]
    rec = next(r for r in caplog.records if "fan-out job failed" in r.message)
    assert rec.levelno == logging.ERROR and rec.exc_info
