"""The one HTTP client allowed to fetch a user-supplied URL.

Every outbound request to a URL a user can influence passes through
:func:`safe_get`. It is deliberately small: follow redirects by hand and check
the destination of *every* hop with :func:`is_publicly_routable` before the
request is sent, so a public page cannot bounce the backend into the LAN or a
cloud metadata endpoint. Bodies are streamed so a hostile server cannot make
the process allocate past ``max_bytes``.

The guard is resolve-then-fetch, like the crawler's; it is not a defence
against DNS rebinding (see ``net_guard``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.utils.crawl.net_guard import is_publicly_routable

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Generic desktop browser identity. No email, no build/session identifier:
# the fetcher must not advertise anything about this deployment.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Wikimedia is the one exception: its published robot policy
# (https://w.wiki/4wJS) requires an identifying User-Agent with contact
# information and answers uncached pages served to a generic browser UA with
# ``403 ... Please respect our robot policy``. The contact is a public GitHub
# profile URL rather than an email address, so nothing private is disclosed.
_WIKIMEDIA_USER_AGENT = (
    "Monoshiri/1.0 (self-hosted personal knowledge base; "
    f"https://github.com/aydenious) python-httpx/{httpx.__version__}"
)

# Wikimedia projects, matched on a label boundary so ``notwikipedia.org`` and
# ``wikipedia.org.evil.com`` stay on the default UA.
_WIKIMEDIA_DOMAINS = (
    "wikipedia.org",
    "wikimedia.org",
    "wiktionary.org",
    "wikibooks.org",
    "wikiquote.org",
    "wikisource.org",
    "wikiversity.org",
    "wikivoyage.org",
    "wikinews.org",
    "wikidata.org",
    "mediawiki.org",
)


class UnsafeUrlError(ValueError):
    """The destination (an initial URL or a redirect hop) is not public."""


class FetchError(RuntimeError):
    """The fetch was refused or failed (status, size, redirects, transport)."""


@dataclass(frozen=True)
class FetchedPage:
    final_url: str
    status: int
    content_type: str
    body: bytes


def user_agent_for(url: str) -> str:
    """The User-Agent to send to *url*'s host.

    Wikimedia projects demand an identifying UA (see above); every other host
    gets the generic browser identity. The check is on the hostname's label
    boundary (trailing dot stripped, case-insensitive), so only a domain equal
    to or a subdomain of a Wikimedia project matches.
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if any(
        host == domain or host.endswith("." + domain) for domain in _WIKIMEDIA_DOMAINS
    ):
        return _WIKIMEDIA_USER_AGENT
    return _USER_AGENT


def is_http_error(exc: BaseException) -> bool:
    """True for an httpx transport/URL failure (a fetch failure to the user).

    Exposed so callers can recognise one without importing httpx themselves —
    the outbound-fetch gate requires this module to be the only HTTP client.
    """
    return isinstance(exc, (httpx.HTTPError, httpx.InvalidURL))


def _ensure_public(url: str) -> None:
    scheme = urlsplit(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"only http and https URLs may be fetched: {url!r}")
    if not is_publicly_routable(url):
        raise UnsafeUrlError(f"URL is not publicly reachable: {url}")


async def safe_get(
    url: str,
    *,
    max_bytes: int = 50 * 1024 * 1024,
    timeout: float = 30.0,
    total_timeout: float = 60.0,
    max_redirects: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchedPage:
    """GET *url*, refusing private destinations on every hop.

    A non-2xx final response or a body larger than *max_bytes* raises
    :class:`FetchError`; a private hop raises :class:`UnsafeUrlError`.

    ``timeout`` is httpx's per-operation timeout, which a slow-drip server can
    evade indefinitely while staying under ``max_bytes``. A separate
    ``total_timeout`` (default 60 s) therefore bounds the whole fetch —
    redirects and body included — using :func:`asyncio.timeout`.
    """
    try:
        async with asyncio.timeout(total_timeout):
            return await _fetch(
                url,
                max_bytes=max_bytes,
                timeout=timeout,
                max_redirects=max_redirects,
                transport=transport,
            )
    except TimeoutError as exc:
        raise FetchError(
            f"fetch exceeded the total deadline ({total_timeout:g}s)"
        ) from exc


async def _fetch(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    max_redirects: int,
    transport: httpx.AsyncBaseTransport | None,
) -> FetchedPage:
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout,
        transport=transport,
        # Ignore HTTP(S)_PROXY: routing through an env-configured proxy would
        # make the resolve-then-fetch check meaningless.
        trust_env=False,
        # Default identity; each request overrides it with user_agent_for(hop).
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        # max_redirects redirects are permitted, so at most that many + 1
        # requests are ever sent. A redirect on the last one trips the limit.
        for _ in range(max_redirects + 1):
            _ensure_public(current)
            try:
                async with client.stream(
                    # The UA is chosen per hop, not once per client: a redirect
                    # can land on a different host, and Wikimedia refuses the
                    # generic browser identity.
                    "GET",
                    current,
                    headers={"User-Agent": user_agent_for(current)},
                ) as response:
                    if response.is_redirect:
                        # httpx only reports a redirect when Location is set.
                        current = urljoin(current, response.headers["location"])
                        continue

                    final_url = str(response.url)
                    status = response.status_code
                    if not 200 <= status < 300:
                        raise FetchError(
                            f"fetch failed with HTTP {status}: {final_url}"
                        )

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError("too large")

                    return FetchedPage(
                        final_url=final_url,
                        status=status,
                        content_type=response.headers.get("content-type", ""),
                        body=bytes(body),
                    )
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                # InvalidURL is not an HTTPError (it subclasses Exception), so
                # a malformed redirect target would otherwise escape raw.
                # UnsupportedProtocol is a TransportError and already covered.
                raise FetchError(f"fetch failed: {exc}") from exc

    raise FetchError(f"too many redirects (limit {max_redirects})")
