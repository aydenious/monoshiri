"""Per-reply budget for web searches and crawled pages.

One reply shares a single budget across every web-capable specialist, so a
research loop cannot spend an unbounded number of live proxy renders. The
budget lives in a ContextVar set once per turn, which is how per-turn state
reaches sub-agent tool calls (the same mechanism as the token accumulator).

Two things keep the model honest about it:

- every web tool result carries :meth:`WebSearchBudget.footer`, so the model
  can see how many searches and crawled pages it has used and how much time
  is left;
- an over-budget call returns :meth:`WebSearchBudget.try_spend`'s (or
  :meth:`WebSearchBudget.try_spend_pages`') refusal text instead of running the
  capability. The text either asks for a smaller retry (the call alone was too
  big) or ends the work for the reply (nothing or no time left).

Searches and crawled pages are counted apart but share one clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass

MAX_WEB_SEARCHES = 12
"""Searches one reply may spend across every specialist."""

MAX_CRAWL_PAGES_PER_REPLY = 20
"""Pages one reply may crawl across every specialist."""

MAX_WEB_SECONDS = 90
"""Wall-clock seconds one reply may spend searching and crawling (starts at the first call)."""

_USED_UP = (
    "Web search budget used up for this reply ({used} of {max} searches used). "
    "Do not search again. Answer now with what you already have, and say "
    "plainly which parts you could not check."
)

_TIME_UP = (
    "Web time limit reached for this reply (about {secs} s of searching and "
    "crawling). Do not search or crawl again. Answer now with what you already "
    "have, and say plainly which parts you could not check."
)

_TOO_BIG = (
    "This search needs {cost} searches but only {left} of {max} are left for "
    "this reply. Retry with at most {left} (fewer queries or pages), or answer "
    "with what you have."
)


_PAGES_USED_UP = (
    "Web page budget used up for this reply ({used} of {max} pages crawled). "
    "Do not crawl again. Answer now with what you already have, and say "
    "plainly which parts you could not check."
)

_PAGES_TOO_BIG = (
    "This crawl could fetch {cost} pages but only {left} of {max} are left for "
    "this reply. Retry with fewer start URLs or a lower maxCrawlPages (at most "
    "{left}), or answer with what you have."
)


def _seconds(value: float) -> int | float:
    """Render a second count without a trailing ``.0`` for whole seconds."""
    return int(value) if float(value).is_integer() else value


@dataclass
class WebSearchBudget:
    """Search allowance for one reply.

    ``started_at`` is set on the first spend, so time spent on retrieval and
    LLM calls before the first web search does not count against the budget.
    """

    max_searches: int = MAX_WEB_SEARCHES
    max_seconds: float = MAX_WEB_SECONDS
    clock: Callable[[], float] = time.monotonic
    used: int = 0
    max_pages: int = MAX_CRAWL_PAGES_PER_REPLY
    pages_used: int = 0
    started_at: float | None = None

    def _elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return self.clock() - self.started_at

    def _refusal(
        self,
        cost: int,
        used: int,
        cap: int,
        used_up: str,
        too_big: str,
    ) -> str | None:
        """Refusal text for spending ``cost`` of one counter, or ``None`` if allowed.

        Precedence, because the model's next move differs: counter used up, then
        time up (both end all further work of that kind), then a call bigger
        than what is left (answered by retrying smaller).
        """
        left = max(0, cap - used)
        if left == 0:
            return used_up.format(used=used, max=cap)
        if self._elapsed() >= self.max_seconds:
            return _TIME_UP.format(secs=_seconds(self.max_seconds))
        if cost > left:
            return too_big.format(cost=cost, left=left, max=cap)
        return None

    def try_spend(self, cost: int) -> str | None:
        """Spend ``cost`` searches, or return the refusal text and spend nothing."""
        refusal = self._refusal(cost, self.used, self.max_searches, _USED_UP, _TOO_BIG)
        if refusal is not None:
            return refusal
        if self.started_at is None:
            self.started_at = self.clock()
        self.used += cost
        return None

    def try_spend_pages(self, cost: int) -> str | None:
        """Spend ``cost`` crawled pages, or return the refusal text and spend nothing."""
        refusal = self._refusal(
            cost, self.pages_used, self.max_pages, _PAGES_USED_UP, _PAGES_TOO_BIG
        )
        if refusal is not None:
            return refusal
        if self.started_at is None:
            self.started_at = self.clock()
        self.pages_used += cost
        return None

    def footer(self) -> str:
        """Trailing line appended to every web tool result for the model to read."""
        left = max(0, round(self.max_seconds - self._elapsed()))
        return (
            f"\n\n[web budget: {self.used} of {self.max_searches} searches, "
            f"{self.pages_used} of {self.max_pages} pages, about {left} s left]"
        )


def web_cost(capability_name: str, payload: dict) -> int | None:
    """Search cost of one capability call, or ``None`` when it is not a web search.

    A cost is the number of live scraper sources the call opens: queries x pages
    for Google, every URL plus every search query for YouTube scrape, and every
    video URL for YouTube comments. Field names follow the capability schemas.
    """
    if capability_name == "google_search.scrape":
        queries = payload.get("queries") or []
        pages = max(1, int(payload.get("max_pages_per_query") or 1))
        return max(1, len(queries) * pages)
    if capability_name == "youtube.scrape":
        urls = payload.get("urls") or []
        queries = payload.get("search_queries") or []
        return max(1, len(urls) + len(queries))
    if capability_name == "youtube.comments":
        return max(1, len(payload.get("urls") or []))
    return None


def crawl_cost(capability_name: str, payload: dict) -> int | None:
    """Page cost of one capability call, or ``None`` when it is not ``web.crawl``.

    The cost is the most pages the call can fetch. ``maxCrawlPages`` caps the
    whole crawl (start URLs included), so depth 0 fetches at most
    ``min(start URLs, maxCrawlPages)`` and a spidering crawl at most
    ``maxCrawlPages``. Missing keys take the schema defaults (10 pages, depth 0).
    """
    if capability_name != "web.crawl":
        return None
    max_pages = max(1, int(payload.get("maxCrawlPages") or 10))
    if int(payload.get("maxCrawlDepth") or 0) > 0:
        return max_pages
    return max(1, min(len(payload.get("startUrls") or []), max_pages))


_turn_budget: ContextVar[WebSearchBudget | None] = ContextVar(
    "_turn_budget", default=None
)


def start_turn_budget() -> WebSearchBudget:
    """Create a fresh budget for the current turn and return it.

    Called once per reply by the chat orchestrators; sub-agent tool calls
    inherit it through the context copy.
    """
    budget = WebSearchBudget()
    _turn_budget.set(budget)
    return budget


def current_budget() -> WebSearchBudget | None:
    return _turn_budget.get()
