"""URL classification, canonicalisation, titles, SRT cleanup and extraction."""

from __future__ import annotations

import pytest

from app.services.url_ingest import (
    NoReadableTextError,
    canonicalize_url,
    classify_url,
    extract_readable,
    extract_title,
    pdf_title_from_url,
    srt_to_text,
    youtube_oembed_url,
)

pytestmark = pytest.mark.unit


VIDEO_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=sharetoken",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=sharetoken",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&index=2",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=30",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "HTTPS://WWW.YOUTUBE.COM/watch?v=dQw4w9WgXcQ",
    ],
)
def test_canonicalize_youtube_variants_share_one_identity(url):
    """Every share-sheet form of one video canonicalises to one URL."""
    assert canonicalize_url(url) == f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTPS://Example.COM/Article", "https://example.com/Article"),
        (
            "https://example.com/article?utm_source=share&utm_medium=iphone&id=7",
            "https://example.com/article?id=7",
        ),
        ("https://example.com/article#section-2", "https://example.com/article"),
        (
            "https://example.com/article?utm_source=x#top",
            "https://example.com/article",
        ),
        # Not a tracking prefix: a query that merely contains "utm" survives.
        ("https://example.com/a?volume=3", "https://example.com/a?volume=3"),
        # Path parameters on the last segment are part of the resource.
        (
            "https://example.com/a;jsessionid=1?x=2",
            "https://example.com/a;jsessionid=1?x=2",
        ),
        ("http://example.com:8080/a", "http://example.com:8080/a"),
    ],
)
def test_canonicalize_generic_url(url, expected):
    assert canonicalize_url(url) == expected


def test_youtube_oembed_url_encodes_the_watch_url():
    oembed = youtube_oembed_url(VIDEO_ID)
    assert oembed.startswith("https://www.youtube.com/oembed?")
    assert "url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D" in oembed
    assert "format=json" in oembed


def test_pdf_title_from_last_segment():
    assert pdf_title_from_url("https://example.com/paper.pdf") == "paper"
    assert pdf_title_from_url("https://example.com/docs/My%20Paper.PDF") == "My Paper"
    assert pdf_title_from_url("https://example.com/") is None
    assert pdf_title_from_url("https://example.com/files/.pdf") is None


def test_extract_title_from_html_without_fetching():
    html = (
        b"<html><head><title>Article</title></head><body>"
        b"<article><h1>Saved page</h1><p>Body</p></article></body></html>"
    )
    assert extract_title(html) == "Saved page"
    assert extract_title(b"<html><body>no title</body></html>") is None


def test_classify_is_cheap_and_never_fetches(monkeypatch):
    """Classification reads the URL only: the request never downloads a page."""

    def must_not_fetch(*_args, **_kwargs):
        raise AssertionError("classify_url must not fetch")

    # Pins "no fetch here": if a safe_get import ever comes back, the call
    # fails loudly instead of silently downloading the page in the request.
    monkeypatch.setattr(
        "app.services.url_ingest.safe_get", must_not_fetch, raising=False
    )

    assert classify_url("https://www.youtube.com/watch?v=abc123") == "youtube"
    assert classify_url("https://youtu.be/abc123") == "youtube"
    assert classify_url("https://example.com/paper.pdf") == "pdf"
    assert classify_url("https://example.com/PAPER.PDF") == "pdf"
    assert classify_url("https://example.com/download") == "page_or_pdf"
    assert classify_url("https://example.com/article") == "page_or_pdf"


def test_srt_to_text_drops_rolling_overlap_only():
    """A line repeated across adjacent cues is overlap; a later comeback is not."""
    srt = """\
1
00:00:01,000 --> 00:00:02,000
Yeah.

2
00:00:02,000 --> 00:00:03,000
Yeah.

3
00:00:03,000 --> 00:00:04,000
So here is the point of the whole video.

4
00:00:04,000 --> 00:00:05,000
So here is the point of the whole video.

5
00:00:05,000 --> 00:00:06,000
And that is all.

6
00:00:06,000 --> 00:00:07,000
Yeah.
"""
    assert srt_to_text(srt) == (
        "Yeah.\nSo here is the point of the whole video.\nAnd that is all.\nYeah."
    )


def test_extract_readable_empty_raises():
    html = (
        b"<html><head><title>App</title></head><body>"
        b'<div id="root"></div><script>var x = 1;</script>'
        b"</body></html>"
    )
    with pytest.raises(NoReadableTextError, match="No readable text"):
        extract_readable(html, "https://example.com/app")
    # Still a ValueError, as the service contract promised.
    assert issubclass(NoReadableTextError, ValueError)


def test_extract_readable_keeps_heading_and_paragraph():
    html = b"""<!DOCTYPE html><html><head><title>Test</title></head><body>
    <nav>menu</nav>
    <main><article>
    <h1>Heading</h1>
    <p>This is a paragraph with enough text to be considered content by the
    extractor. It has several words.</p>
    <p>Second paragraph here.</p>
    </article></main><footer>foot</footer></body></html>"""

    text = extract_readable(html, "https://example.com/article")

    assert "# Heading" in text
    assert "This is a paragraph with enough text" in text
