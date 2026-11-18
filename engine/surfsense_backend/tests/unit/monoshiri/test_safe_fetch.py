"""SSRF-safe fetch: per-hop destination checks, redirect cap and body limit."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.utils.crawl import safe_fetch
from app.utils.crawl.safe_fetch import FetchError, UnsafeUrlError, safe_get

pytestmark = pytest.mark.unit

PUBLIC = "https://public.example/"
PRIVATE = "http://192.168.1.10/"


def _allow(monkeypatch, *, public: bool = True) -> None:
    monkeypatch.setattr(
        safe_fetch,
        "is_publicly_routable",
        lambda url: public and url.startswith(PUBLIC),
    )


def _no_request(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("no request may be sent")


WIKI = "https://en.wikipedia.org/wiki/X"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (WIKI, safe_fetch._WIKIMEDIA_USER_AGENT),
        ("https://commons.wikimedia.org/x", safe_fetch._WIKIMEDIA_USER_AGENT),
        ("https://WWW.WIKIDATA.ORG./x", safe_fetch._WIKIMEDIA_USER_AGENT),
        ("https://example.com/", safe_fetch._USER_AGENT),
        # Label boundary, not a substring: these merely contain a project name.
        ("https://notwikipedia.org/", safe_fetch._USER_AGENT),
        ("https://wikipedia.org.evil.com/", safe_fetch._USER_AGENT),
    ],
)
def test_user_agent_for(url, expected):
    assert safe_fetch.user_agent_for(url) == expected


async def test_private_initial_url_refused(monkeypatch):
    _allow(monkeypatch, public=False)
    transport = httpx.MockTransport(_no_request)

    with pytest.raises(UnsafeUrlError):
        await safe_get(PRIVATE, transport=transport)


async def test_redirect_to_private_refused(monkeypatch):
    _allow(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": PRIVATE})

    with pytest.raises(UnsafeUrlError):
        await safe_get(PUBLIC + "start", transport=httpx.MockTransport(handler))


async def test_redirect_limit(monkeypatch):
    _allow(monkeypatch)
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        hop = int(request.url.path.removeprefix("/hop"))
        return httpx.Response(302, headers={"location": f"{PUBLIC}hop{hop + 1}"})

    with pytest.raises(FetchError, match="redirect"):
        await safe_get(
            PUBLIC + "hop0", max_redirects=5, transport=httpx.MockTransport(handler)
        )

    # The initial URL plus five permitted redirects: the sixth redirect is the
    # one that trips the limit, so no seventh request is ever sent.
    assert len(sent) == 6


async def test_size_cap_aborts_stream(monkeypatch):
    _allow(monkeypatch)

    class ChunkedBody(httpx.AsyncByteStream):
        """Ten 8-byte chunks; records which ones the fetcher consumed."""

        def __init__(self) -> None:
            self.chunks = [b"x" * 8] * 10
            self.consumed: list[int] = []

        async def __aiter__(self):
            for index, chunk in enumerate(self.chunks):
                self.consumed.append(index)
                yield chunk

    body = ChunkedBody()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    with pytest.raises(FetchError, match="too large"):
        await safe_get(
            PUBLIC + "big", max_bytes=10, transport=httpx.MockTransport(handler)
        )

    # Two chunks (16 bytes) cross the 10-byte cap; the remaining eight are
    # never read, so the stream was abandoned rather than drained.
    assert body.consumed == [0, 1]
    assert len(body.consumed) < len(body.chunks)


async def test_non_http_scheme_refused(monkeypatch):
    _allow(monkeypatch)
    transport = httpx.MockTransport(_no_request)

    for url in ("file:///etc/passwd", "gopher://public.example/"):
        with pytest.raises(UnsafeUrlError):
            await safe_get(url, transport=transport)


async def test_ok_returns_final_url_after_redirect(monkeypatch):
    _allow(monkeypatch)
    seen_user_agent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_user_agent.append(request.headers["user-agent"])
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html>ok</html>",
        )

    page = await safe_get(PUBLIC + "start", transport=httpx.MockTransport(handler))

    assert page.final_url == PUBLIC + "final"
    assert page.status == 200
    assert page.content_type.startswith("text/html")
    assert page.body == b"<html>ok</html>"
    # Generic browser identity: no email or personal identifier.
    assert "Mozilla" in seen_user_agent[-1]
    assert "@" not in seen_user_agent[-1]


async def test_user_agent_follows_the_redirect_to_wikimedia(monkeypatch):
    """UA is per hop: the generic identity on hop 1, Wikimedia's on hop 2."""
    allowed = (PUBLIC, WIKI)
    monkeypatch.setattr(
        safe_fetch, "is_publicly_routable", lambda url: url.startswith(allowed)
    )
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers["user-agent"]))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": WIKI})
        return httpx.Response(200, content=b"<html>ok</html>")

    page = await safe_get(PUBLIC + "start", transport=httpx.MockTransport(handler))

    assert page.final_url == WIKI
    assert seen == [
        (PUBLIC + "start", safe_fetch._USER_AGENT),
        (WIKI, safe_fetch._WIKIMEDIA_USER_AGENT),
    ]


async def test_non_2xx_final_status_raises(monkeypatch):
    _allow(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(FetchError):
        await safe_get(PUBLIC + "missing", transport=httpx.MockTransport(handler))


async def test_malformed_redirect_target_becomes_fetch_error(monkeypatch):
    """``httpx.InvalidURL`` is not an ``HTTPError``; it must not escape raw."""
    _allow(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("bad redirect target")

    with pytest.raises(FetchError):
        await safe_get(PUBLIC + "start", transport=httpx.MockTransport(handler))


async def test_client_ignores_proxy_environment(monkeypatch):
    """A configured HTTP(S)_PROXY must not reroute the fetch."""
    _allow(monkeypatch)
    captured: dict = {}
    real_client = httpx.AsyncClient

    def recording_client(*args, **kwargs):
        captured.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", recording_client)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    await safe_get(PUBLIC + "ok", transport=httpx.MockTransport(handler))

    assert captured["trust_env"] is False


async def test_total_deadline_bounds_a_slow_server(monkeypatch):
    """The per-operation timeout cannot stop a slow drip; the total one can."""
    _allow(monkeypatch)

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, _request: httpx.Request):
            await asyncio.sleep(5)
            raise AssertionError("the deadline should have cancelled this")

    with pytest.raises(FetchError, match="total deadline"):
        await safe_get(PUBLIC + "drip", total_timeout=0.01, transport=SlowTransport())
