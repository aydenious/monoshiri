"""Classification and readable-text extraction for a saved URL.

No route code lives here: the route decides permission, identity and the
document row, while the Celery task calls these helpers. Fetching goes only
through :func:`app.utils.crawl.safe_fetch.safe_get` (see the outbound-fetch
gate), so a URL cannot reach a private address. Classification makes no
request at all, so the HTTP handler never downloads a page.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import unquote, unquote_plus, urlencode, urlparse, urlunparse

import trafilatura

from app.proprietary.platforms.youtube.url_resolver import get_youtube_video_id

# "page_or_pdf" means "an ordinary URL whose bytes are only known once the
# ingest task fetches it"; the task branches on the PDF magic bytes.
UrlKind = Literal["youtube", "pdf", "page_or_pdf"]

YOUTUBE_WATCH_PREFIX = "https://www.youtube.com/watch?v="
YOUTUBE_OEMBED_ENDPOINT = "https://www.youtube.com/oembed"

_TRACKING_PREFIX = "utm_"


class NoReadableTextError(ValueError):
    """The page yielded no readable text (usually JavaScript-rendered)."""


def classify_url(url: str) -> UrlKind:
    """Cheaply decide what a URL looks like: YouTube, a PDF path, or neither.

    No request is made here. A ``.pdf`` path is trusted without a fetch; every
    other non-YouTube URL is ``page_or_pdf`` and the ingest task reads the
    content type from the single fetch it performs anyway. Classifying by
    fetching (as an earlier version did) downloaded the page twice and could
    block the request for the length of the download.
    """
    if get_youtube_video_id(url):
        return "youtube"
    if urlparse(url).path.lower().endswith(".pdf"):
        return "pdf"
    return "page_or_pdf"


def _is_tracking_param(part: str) -> bool:
    key = unquote_plus(part.split("=", 1)[0]).lower()
    return key.startswith(_TRACKING_PREFIX)


def canonicalize_url(url: str) -> str:
    """Return the one stable identity a URL is hashed and stored under.

    The share sheet makes many URLs for one resource: a per-share ``?si=``
    token on YouTube, ``utm_*`` tracking parameters, a fragment, a different
    letter case. Canonicalising before hashing is what makes a repeat share
    find the row the first share created. Every recognised YouTube shape is
    mapped to the canonical watch URL; other URLs keep their path and their
    non-tracking query, with the scheme and host lowercased.
    """
    video_id = get_youtube_video_id(url)
    if video_id:
        return f"{YOUTUBE_WATCH_PREFIX}{video_id}"

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    # IPv6 literals keep their brackets; a port survives only when explicit.
    netloc = f"[{host}]" if ":" in host else host
    try:
        port = parsed.port
    except ValueError:  # malformed port: leave it out of the identity
        port = None
    if port is not None:
        netloc = f"{netloc}:{port}"

    query = "&".join(
        part
        for part in parsed.query.split("&")
        if part and not _is_tracking_param(part)
    )
    return urlunparse((scheme, netloc, parsed.path, parsed.params, query, ""))


def youtube_oembed_url(video_id: str) -> str:
    """The oEmbed endpoint for a video's title (fetched through ``safe_get``)."""
    watch_url = f"{YOUTUBE_WATCH_PREFIX}{video_id}"
    return (
        f"{YOUTUBE_OEMBED_ENDPOINT}?{urlencode({'url': watch_url, 'format': 'json'})}"
    )


def pdf_title_from_url(url: str) -> str | None:
    """A readable title for a PDF link: its last path segment without ``.pdf``."""
    path = urlparse(url).path.rstrip("/")
    segment = unquote(path.rsplit("/", 1)[-1]).strip()
    if segment.lower().endswith(".pdf"):
        segment = segment[: -len(".pdf")].strip()
    return segment or None


def extract_title(html: bytes) -> str | None:
    """The page title from the HTML already in memory; never fetches.

    ``trafilatura.extract_metadata`` reads ``<title>``/OpenGraph/``<h1>``.
    Any failure (or no title) is ``None`` so the caller falls back to the URL.
    """
    try:
        metadata = trafilatura.extract_metadata(html.decode("utf-8", errors="replace"))
    except Exception:
        return None
    title = getattr(metadata, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def srt_to_text(srt: str) -> str:
    """Strip SRT indices/timestamps and rolling caption overlap.

    YouTube's generated captions repeat a line across consecutive cues while
    it is being spoken. Only that overlap is dropped (a line equal to one of
    the previous two); a refrain that recurs later in the transcript is real
    speech and is kept.
    """
    lines: list[str] = []
    for raw in srt.splitlines():
        line = raw.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        if line in lines[-2:]:
            continue
        lines.append(line)
    return "\n".join(lines)


def extract_readable(html: bytes, url: str) -> str:
    """Extract the main readable content as Markdown, or raise ``ValueError``."""
    text = trafilatura.extract(
        html.decode("utf-8", errors="replace"),
        url=url,
        output_format="markdown",
        include_links=True,
    )
    if not text or not text.strip():
        raise NoReadableTextError("No readable text (page may need JavaScript)")
    return text
