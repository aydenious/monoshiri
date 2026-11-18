"""One hard web-search budget per chat reply.

The budget caps searches and search time per reply, shows itself to the model
in every web tool result, and refuses (without running the capability) once
spent. These tests pin the arithmetic, the exact user-visible strings, the
ContextVar hop into the tasks that run tools, the capability-tool hook, the
entry points that open a budget, and the prompt text that must match the code.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, Field

from app.agents.chat.multi_agent_chat.shared import web_budget
from app.agents.chat.multi_agent_chat.shared.web_budget import (
    MAX_CRAWL_PAGES_PER_REPLY,
    MAX_WEB_SEARCHES,
    MAX_WEB_SECONDS,
    WebSearchBudget,
    crawl_cost,
    current_budget,
    start_turn_budget,
    web_cost,
)
from app.capabilities.core.types import Capability
from app.capabilities.web.crawl.schemas import CrawlInput

pytestmark = pytest.mark.unit

REFUSAL_START = "Web search budget used up"
STOP_HINT = "Do not search again"
PAGES_STOP_HINT = "Do not crawl again"
TIME_STOP_HINT = "Do not search or crawl again"

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PROMPT_ROOT = BACKEND_ROOT / "app/agents/chat/multi_agent_chat"
ROUTING_PROMPT = PROMPT_ROOT / "main_agent/system_prompt/prompts/routing.md"
GOOGLE_SEARCH_PROMPT = PROMPT_ROOT / "subagents/builtins/google_search/system_prompt.md"
YOUTUBE_PROMPT = PROMPT_ROOT / "subagents/builtins/youtube/system_prompt.md"

NEW_CHAT_ORCHESTRATOR = (
    BACKEND_ROOT / "app/tasks/chat/streaming/flows/new_chat/orchestrator.py"
)
RESUME_CHAT_ORCHESTRATOR = (
    BACKEND_ROOT / "app/tasks/chat/streaming/flows/resume_chat/orchestrator.py"
)
AUTOMATION_AGENT_TASK = (
    BACKEND_ROOT / "app/automations/actions/builtin/agent_task/invoke.py"
)
TURN_ENTRY_POINTS = [
    NEW_CHAT_ORCHESTRATOR,
    RESUME_CHAT_ORCHESTRATOR,
    AUTOMATION_AGENT_TASK,
]


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _no_budget_left_over():
    """Each test starts with no turn budget installed in its context."""
    token = web_budget._turn_budget.set(None)
    yield
    web_budget._turn_budget.reset(token)


# ---------------------------------------------------------------------------
# Budget arithmetic
# ---------------------------------------------------------------------------


def test_spend_until_cap():
    budget = WebSearchBudget(clock=FakeClock())

    for _ in range(MAX_WEB_SEARCHES):
        assert budget.try_spend(1) is None

    assert budget.used == MAX_WEB_SEARCHES
    assert budget.try_spend(1) == (
        f"Web search budget used up for this reply ({MAX_WEB_SEARCHES} of "
        f"{MAX_WEB_SEARCHES} searches used). Do not search again. Answer now "
        "with what you already have, and say plainly which parts you could not "
        "check."
    )
    assert budget.used == MAX_WEB_SEARCHES


def test_only_the_stop_refusals_say_do_not_search_again():
    """The prompts key off this phrase: it must mean "stop", never "retry"."""
    assert STOP_HINT in web_budget._USED_UP
    assert PAGES_STOP_HINT in web_budget._PAGES_USED_UP
    assert TIME_STOP_HINT in web_budget._TIME_UP
    for text in (web_budget._TOO_BIG, web_budget._PAGES_TOO_BIG):
        assert "Do not " not in text


def test_cost_larger_than_left_asks_for_a_smaller_retry():
    """A call that overshoots is a sizing problem, not the end of the budget."""
    budget = WebSearchBudget(clock=FakeClock(), used=10)

    refusal = budget.try_spend(3)

    assert refusal == (
        "This search needs 3 searches but only 2 of 12 are left for this "
        "reply. Retry with at most 2 (fewer queries or pages), or answer with "
        "what you have."
    )
    assert STOP_HINT not in refusal
    assert budget.used == 10
    # Spending exactly what is left still fits.
    assert budget.try_spend(2) is None
    assert budget.used == 12


def test_time_limit():
    clock = FakeClock()
    budget = WebSearchBudget(clock=clock)

    assert budget.try_spend(1) is None  # t=0 opens the window
    clock.advance(MAX_WEB_SECONDS)  # t=90

    refusal = budget.try_spend(1)
    assert refusal == (
        f"Web time limit reached for this reply (about {MAX_WEB_SECONDS} s of "
        "searching and crawling). Do not search or crawl again. Answer now with "
        "what you already have, and say plainly which parts you could not check."
    )
    assert budget.used == 1

    # The clock does not run until the first spend.
    late = WebSearchBudget(clock=FakeClock(now=500.0))
    assert late.try_spend(1) is None
    assert late.started_at == 500.0


def test_time_limit_outranks_an_oversized_call():
    """Once time is up the model stops; a cheaper retry would not change that."""
    clock = FakeClock()
    budget = WebSearchBudget(clock=clock, used=10)
    assert budget.try_spend(1) is None  # opens the window at t=0
    clock.advance(MAX_WEB_SECONDS)

    refusal = budget.try_spend(5)

    assert refusal is not None
    assert "time limit reached" in refusal
    assert TIME_STOP_HINT in refusal
    assert budget.used == 11


def test_used_up_outranks_the_time_limit():
    clock = FakeClock()
    budget = WebSearchBudget(clock=clock)
    for _ in range(MAX_WEB_SEARCHES):
        assert budget.try_spend(1) is None
    clock.advance(MAX_WEB_SECONDS)

    refusal = budget.try_spend(1)
    assert refusal is not None and refusal.startswith(REFUSAL_START)


def test_footer_text():
    clock = FakeClock()
    budget = WebSearchBudget(clock=clock)

    assert budget.footer() == (
        "\n\n[web budget: 0 of 12 searches, 0 of 20 pages, about 90 s left]"
    )
    for _ in range(3):
        assert budget.try_spend(1) is None
    clock.advance(30)

    assert budget.footer() == (
        "\n\n[web budget: 3 of 12 searches, 0 of 20 pages, about 60 s left]"
    )


def test_web_cost():
    assert (
        web_cost(
            "google_search.scrape", {"queries": ["a", "b"], "max_pages_per_query": 3}
        )
        == 6
    )
    assert web_cost("google_search.scrape", {"queries": ["a", "b"]}) == 2
    # YouTube scrape pays for every URL and every search query it opens.
    assert web_cost("youtube.scrape", {"search_queries": [], "urls": ["u"]}) == 1
    assert web_cost("youtube.scrape", {"search_queries": ["a", "b"]}) == 2
    assert (
        web_cost(
            "youtube.scrape",
            {"urls": ["u1", "u2", "u3"], "search_queries": ["a", "b"]},
        )
        == 5
    )
    # Comments pay per video URL, so one call cannot harvest 20 videos for free.
    assert web_cost("youtube.comments", {"urls": ["u"]}) == 1
    assert web_cost("youtube.comments", {"urls": [f"u{i}" for i in range(20)]}) == 20
    assert web_cost("web_crawler.crawl", {"urls": ["u"]}) is None
    assert web_cost("reddit.scrape", {"queries": ["a"]}) is None


# ---------------------------------------------------------------------------
# Crawled-page budget
# ---------------------------------------------------------------------------

URLS = [f"https://example.com/{i}" for i in range(30)]


def test_crawl_cost_ignores_other_capabilities():
    assert crawl_cost("google_search.scrape", {"queries": ["a"]}) is None
    assert crawl_cost("web_crawler.crawl", {"startUrls": URLS[:2]}) is None


def test_crawl_cost_depth_zero_is_the_start_urls_capped_by_max_pages():
    assert crawl_cost("web.crawl", {"startUrls": URLS[:3]}) == 3
    assert crawl_cost("web.crawl", {"startUrls": URLS, "maxCrawlPages": 5}) == 5
    assert (
        crawl_cost(
            "web.crawl",
            {"startUrls": URLS[:3], "maxCrawlDepth": 0, "maxCrawlPages": 10},
        )
        == 3
    )


def test_crawl_cost_with_depth_is_max_pages():
    payload = {"startUrls": URLS[:1], "maxCrawlDepth": 2, "maxCrawlPages": 15}
    assert crawl_cost("web.crawl", payload) == 15


def test_crawl_cost_uses_schema_defaults_and_is_at_least_one():
    # Default maxCrawlPages is 10, default depth is 0.
    assert crawl_cost("web.crawl", {"startUrls": URLS}) == 10
    assert crawl_cost("web.crawl", {"startUrls": URLS[:1], "maxCrawlDepth": 1}) == 10
    assert crawl_cost("web.crawl", {}) == 1
    assert crawl_cost("web.crawl", {"startUrls": []}) == 1


def test_pages_spend_up_to_exactly_the_cap():
    budget = WebSearchBudget(clock=FakeClock())
    for _ in range(MAX_CRAWL_PAGES_PER_REPLY):
        assert budget.try_spend_pages(1) is None
    assert budget.pages_used == MAX_CRAWL_PAGES_PER_REPLY
    assert budget.try_spend_pages(1) == (
        f"Web page budget used up for this reply ({MAX_CRAWL_PAGES_PER_REPLY} "
        f"of {MAX_CRAWL_PAGES_PER_REPLY} pages crawled). Do not crawl again. "
        "Answer now with what you already have, and say plainly which parts "
        "you could not check."
    )
    assert budget.pages_used == MAX_CRAWL_PAGES_PER_REPLY


def test_a_crawl_bigger_than_what_is_left_spends_nothing():
    budget = WebSearchBudget(clock=FakeClock(), pages_used=18)
    refusal = budget.try_spend_pages(5)
    assert refusal == (
        "This crawl could fetch 5 pages but only 2 of 20 are left for this "
        "reply. Retry with fewer start URLs or a lower maxCrawlPages (at most "
        "2), or answer with what you have."
    )
    assert "Do not crawl again" not in refusal
    assert budget.pages_used == 18
    assert budget.started_at is None
    assert budget.try_spend_pages(2) is None
    assert budget.pages_used == 20


def test_page_time_limit_applies_even_with_pages_left():
    clock = FakeClock()
    budget = WebSearchBudget(clock=clock)
    assert budget.try_spend_pages(1) is None
    clock.advance(MAX_WEB_SECONDS)
    refusal = budget.try_spend_pages(1)
    assert refusal == web_budget._TIME_UP.format(secs=MAX_WEB_SECONDS)
    assert budget.pages_used == 1


def test_a_page_spend_starts_the_shared_clock_for_searches():
    clock = FakeClock(now=100.0)
    budget = WebSearchBudget(clock=clock)
    assert budget.try_spend_pages(2) is None
    assert budget.started_at == 100.0
    clock.advance(MAX_WEB_SECONDS)
    assert budget.try_spend(1) == web_budget._TIME_UP.format(secs=MAX_WEB_SECONDS)
    assert budget.used == 0


def test_pages_and_searches_are_counted_apart():
    budget = WebSearchBudget(clock=FakeClock())
    assert budget.try_spend(MAX_WEB_SEARCHES) is None
    assert budget.pages_used == 0
    assert budget.try_spend_pages(3) is None
    assert budget.used == MAX_WEB_SEARCHES
    assert budget.pages_used == 3

    other = WebSearchBudget(clock=FakeClock())
    assert other.try_spend_pages(MAX_CRAWL_PAGES_PER_REPLY) is None
    assert other.try_spend(1) is None
    assert other.used == 1
    assert other.pages_used == MAX_CRAWL_PAGES_PER_REPLY


def test_pages_used_up_outranks_the_time_limit():
    clock = FakeClock()
    budget = WebSearchBudget(clock=clock)
    assert budget.try_spend_pages(MAX_CRAWL_PAGES_PER_REPLY) is None
    clock.advance(MAX_WEB_SECONDS)

    refusal = budget.try_spend_pages(1)
    assert refusal is not None
    assert refusal.startswith("Web page budget used up")


def test_page_time_limit_outranks_an_oversized_crawl():
    clock = FakeClock()
    budget = WebSearchBudget(clock=clock, pages_used=18)
    assert budget.try_spend_pages(1) is None  # opens the window at t=0
    clock.advance(MAX_WEB_SECONDS)

    refusal = budget.try_spend_pages(5)
    assert refusal == web_budget._TIME_UP.format(secs=MAX_WEB_SECONDS)
    assert budget.pages_used == 19


def test_footer_shows_both_counters():
    clock = FakeClock()
    budget = WebSearchBudget(clock=clock)
    assert budget.try_spend(2) is None
    assert budget.try_spend_pages(7) is None
    clock.advance(30)
    assert budget.footer() == (
        "\n\n[web budget: 2 of 12 searches, 7 of 20 pages, about 60 s left]"
    )


async def test_budget_reaches_tool_tasks_spawned_while_the_reply_streams():
    """Production shape: one task iterates the reply generator while the tools
    run in child tasks it spawns; all of them see the one budget object."""
    opened: list[WebSearchBudget] = []
    seen: list[WebSearchBudget | None] = []

    async def reply_stream():
        opened.append(start_turn_budget())
        yield "chunk-0"

        async def tool_call() -> None:
            child = current_budget()
            assert child is not None
            seen.append(child)
            assert child.try_spend(1) is None

        await asyncio.gather(tool_call(), tool_call(), tool_call())
        yield "chunk-1"

    async def streaming_response() -> None:
        async for _ in reply_stream():
            pass

    # The response task is created before any budget exists; the generator body
    # (which sets it) runs inside that task, as StreamingResponse iterates it.
    await asyncio.create_task(streaming_response())

    assert len(opened) == 1
    budget = opened[0]
    assert seen == [budget, budget, budget]
    assert budget.used == 3


# ---------------------------------------------------------------------------
# Capability-tool hook
# ---------------------------------------------------------------------------


class _SearchInput(BaseModel):
    queries: list[str] = Field(min_length=1)
    max_pages_per_query: int = Field(default=1, ge=1)


class _SearchOutput(BaseModel):
    items: list[str] = Field(default_factory=list)


def _search_capability(
    calls: list[object], items: list[str] | None = None
) -> Capability:
    async def _executor(payload: _SearchInput) -> _SearchOutput:
        calls.append(payload)
        return _SearchOutput(items=items if items is not None else ["hit"])

    return Capability(
        name="google_search.scrape",
        description="Search Google.",
        input_schema=_SearchInput,
        output_schema=_SearchOutput,
        executor=_executor,
    )


class _FakeSessionCtx:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def isolate(monkeypatch):
    """Stub the run recorder's session so tools never touch the DB.

    Same harness as ``tests/unit/capabilities/access/test_agent_tools.py``: the
    fake session makes ``record_run`` fail, so the tool returns its plain
    output instead of a ``Command``.
    """
    from app.capabilities.core.access import agent as mod

    monkeypatch.setattr(mod, "async_session_maker", lambda: _FakeSessionCtx())
    return SimpleNamespace(module=mod)


def _search_tool(isolate, calls: list[object], items: list[str] | None = None):
    tools = isolate.module.build_capability_tools(
        workspace_id=7, capabilities=[_search_capability(calls, items)]
    )
    return next(t for t in tools if t.name == "google_search_scrape")


def _invoke(tool, **kwargs):
    runtime = SimpleNamespace(state={}, tool_call_id="tc_1", context=None)
    return tool.coroutine(runtime, **kwargs)


async def test_hook_refuses_when_budget_is_spent(isolate):
    calls: list[object] = []
    tool = _search_tool(isolate, calls)
    budget = start_turn_budget()
    budget.used = MAX_WEB_SEARCHES

    result = await _invoke(tool, queries=["anything"])

    assert isinstance(result, str)
    assert REFUSAL_START in result
    assert calls == []


async def test_hook_appends_footer_on_success(isolate):
    calls: list[object] = []
    tool = _search_tool(isolate, calls)
    budget = start_turn_budget()

    result = await _invoke(tool, queries=["anything"])

    assert len(calls) == 1
    assert budget.used == 1
    assert "[web budget: 1 of 12 searches, 0 of 20 pages" in json.dumps(result)


async def test_hook_charges_the_full_payload_cost(isolate):
    calls: list[object] = []
    tool = _search_tool(isolate, calls)
    budget = start_turn_budget()

    await _invoke(tool, queries=["a", "b"], max_pages_per_query=3)

    assert budget.used == 6


async def test_hook_asks_for_a_smaller_retry_when_the_call_is_too_big(isolate):
    """The call did not run, so the model may retry with what is left."""
    calls: list[object] = []
    tool = _search_tool(isolate, calls)
    budget = start_turn_budget()
    budget.used = MAX_WEB_SEARCHES - 1

    result = await _invoke(tool, queries=["a", "b"], max_pages_per_query=2)

    assert result == (
        f"This search needs 4 searches but only 1 of {MAX_WEB_SEARCHES} are "
        "left for this reply. Retry with at most 1 (fewer queries or pages), "
        "or answer with what you have."
    )
    assert STOP_HINT not in result
    assert calls == []
    assert budget.used == MAX_WEB_SEARCHES - 1


async def test_hook_leaves_output_alone_without_a_budget(isolate):
    calls: list[object] = []
    tool = _search_tool(isolate, calls)

    assert current_budget() is None
    result = await _invoke(tool, queries=["anything"])

    assert result == {"items": ["hit"]}
    assert len(calls) == 1


async def test_hook_ignores_non_web_capabilities(isolate):
    calls: list[object] = []
    capability = Capability(
        name="web_crawler.crawl",
        description="Crawl.",
        input_schema=_SearchInput,
        output_schema=_SearchOutput,
        executor=_search_capability(calls).executor,
    )
    tools = isolate.module.build_capability_tools(
        workspace_id=7, capabilities=[capability]
    )
    tool = next(t for t in tools if t.name == "web_crawler_crawl")
    budget = start_turn_budget()

    result = await _invoke(tool, queries=["anything"])

    assert result == {"items": ["hit"]}
    assert budget.used == 0


def _crawl_tool(isolate, calls: list[object]):
    async def _executor(payload: CrawlInput) -> _SearchOutput:
        calls.append(payload)
        return _SearchOutput(items=["page"])

    capability = Capability(
        name="web.crawl",
        description="Crawl.",
        input_schema=CrawlInput,
        output_schema=_SearchOutput,
        executor=_executor,
    )
    tools = isolate.module.build_capability_tools(
        workspace_id=7, capabilities=[capability]
    )
    return next(t for t in tools if t.name == "web_crawl")


async def test_hook_refuses_a_crawl_over_the_page_budget(isolate):
    calls: list[object] = []
    tool = _crawl_tool(isolate, calls)
    budget = start_turn_budget()
    budget.pages_used = MAX_CRAWL_PAGES_PER_REPLY

    result = await _invoke(tool, startUrls=["https://example.com/a"])

    assert isinstance(result, str)
    assert result.startswith("Web page budget used up")
    assert calls == []


async def test_hook_asks_for_a_smaller_crawl_when_it_is_too_big(isolate):
    calls: list[object] = []
    tool = _crawl_tool(isolate, calls)
    budget = start_turn_budget()
    budget.pages_used = MAX_CRAWL_PAGES_PER_REPLY - 2

    result = await _invoke(tool, startUrls=URLS[:5])

    assert result.startswith("This crawl could fetch 5 pages but only 2 of 20")
    assert calls == []
    assert budget.pages_used == MAX_CRAWL_PAGES_PER_REPLY - 2


async def test_hook_charges_a_crawl_and_appends_the_footer(isolate):
    calls: list[object] = []
    tool = _crawl_tool(isolate, calls)
    budget = start_turn_budget()

    result = await _invoke(tool, startUrls=URLS[:3])

    assert len(calls) == 1
    assert budget.pages_used == 3
    assert budget.used == 0
    assert "[web budget: 0 of 12 searches, 3 of 20 pages" in json.dumps(result)


# ---------------------------------------------------------------------------
# Capability-tool hook: stored-run (``Command``) return path
# ---------------------------------------------------------------------------


def _stored_run(isolate, monkeypatch) -> None:
    """Make ``record_run`` succeed, so the tool returns a ``Command``."""
    monkeypatch.setattr(isolate.module, "record_run", AsyncMock(return_value="abc-123"))


async def test_hook_footer_lands_before_the_citation_label(isolate, monkeypatch):
    """The stored-run path appends the footer, then the citation label last."""
    from langgraph.types import Command

    calls: list[object] = []
    tool = _search_tool(isolate, calls)
    _stored_run(isolate, monkeypatch)
    start_turn_budget()

    result = await _invoke(tool, queries=["anything"])

    assert isinstance(result, Command)
    content = result.update["messages"][0].content
    assert "run_abc-123" in content
    footer_at = content.index(f"[web budget: 1 of {MAX_WEB_SEARCHES} searches")
    label_at = content.index("Cite this scraper run as [1]")
    assert footer_at < label_at


async def test_hook_footer_follows_an_oversized_preview(isolate, monkeypatch):
    """Output over the inline cap returns a preview; the footer still rides along."""
    from langgraph.types import Command

    calls: list[object] = []
    tool = _search_tool(isolate, calls, items=["x" * 40, "y" * 40])
    _stored_run(isolate, monkeypatch)
    monkeypatch.setattr(isolate.module, "RUN_OUTPUT_CHAR_CAP", 50)
    start_turn_budget()

    result = await _invoke(tool, queries=["anything"])

    assert isinstance(result, Command)
    content = result.update["messages"][0].content
    preview_at = content.index("...Showing 1 of 2 items")
    footer_at = content.index(f"[web budget: 1 of {MAX_WEB_SEARCHES} searches")
    label_at = content.index("Cite this scraper run as [1]")
    assert preview_at < footer_at < label_at
    assert "run_abc-123" in content


# ---------------------------------------------------------------------------
# Wiring: every reply opens a budget
# ---------------------------------------------------------------------------


def _start_turn_budget_calls(path: Path) -> list[ast.Call]:
    """Every ``start_turn_budget(...)`` call in a module, via its AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "start_turn_budget":
                calls.append(node)
    return calls


@pytest.mark.parametrize("path", TURN_ENTRY_POINTS, ids=lambda p: p.parent.name)
def test_every_turn_entry_point_opens_a_budget(path):
    """Dropping the call silently disarms the budget for that flow (fail-open)."""
    assert len(_start_turn_budget_calls(path)) == 1
    assert f"from {web_budget.__name__} import start_turn_budget" in path.read_text(
        encoding="utf-8"
    )


def test_automation_opens_its_budget_before_the_first_invoke():
    """Every HITL resume of the run shares the budget opened before ``ainvoke``."""
    tree = ast.parse(AUTOMATION_AGENT_TASK.read_text(encoding="utf-8"))
    budget_lines = [
        call.lineno for call in _start_turn_budget_calls(AUTOMATION_AGENT_TASK)
    ]
    invoke_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ainvoke"
    ]
    assert budget_lines
    assert invoke_lines
    assert max(budget_lines) < min(invoke_lines)


# ---------------------------------------------------------------------------
# Prompts: they must state the same limits, refusals and rules as the code
# ---------------------------------------------------------------------------


def _flat(path: Path) -> str:
    """The prompt with runs of whitespace collapsed, so wrapping does not matter."""
    return " ".join(path.read_text(encoding="utf-8").split())


def _paragraph(path: Path, needle: str) -> str:
    """The blank-line-separated paragraph of ``path`` that contains ``needle``."""
    for paragraph in path.read_text(encoding="utf-8").split("\n\n"):
        if needle in paragraph:
            return " ".join(paragraph.split())
    raise AssertionError(f"no paragraph of {path} contains {needle!r}")


TOO_BIG_LEAD = web_budget._TOO_BIG.split("{cost}")[0].strip()


def test_routing_prompt_states_the_code_budget():
    """If the limits change in code, the supervisor prompt has to change too."""
    text = _flat(ROUTING_PROMPT)
    assert f"{MAX_WEB_SEARCHES} web searches" in text
    assert f"{MAX_WEB_SECONDS} seconds" in text


def test_routing_prompt_widening_stays_within_one_delegation():
    """Requested-N may not invite a second specialist call outside the rules."""
    paragraph = _paragraph(ROUTING_PROMPT, "widen the discovery")
    assert "another specialist call" not in paragraph
    assert "second delegation" in paragraph
    assert "budget" in paragraph


def test_google_search_prompt_prices_extra_pages_like_extra_queries():
    """Under the budget a page costs one search, so paging is not 'cheaper'."""
    lines = [
        line
        for line in GOOGLE_SEARCH_PROMPT.read_text(encoding="utf-8").splitlines()
        if "max_pages_per_query" in line
    ]
    assert lines
    assert not any("cheaper" in line for line in lines)
    assert any("budget" in line for line in lines)


TOO_BIG_LEAD = web_budget._TOO_BIG.split("{cost}")[0].strip()


TOO_BIG_LEAD = web_budget._TOO_BIG.split("{cost}")[0].strip()


@pytest.mark.parametrize(
    "path",
    [GOOGLE_SEARCH_PROMPT, YOUTUBE_PROMPT],
    ids=lambda p: p.parent.name,
)
def test_subagent_prompts_separate_retry_smaller_from_stop(path):
    """The budget section must teach both refusal shapes, keyed off their text."""
    text = _flat(path)
    assert TOO_BIG_LEAD in text  # "... retry with fewer queries/pages"
    assert STOP_HINT in text  # "... stop searching"
    assert TIME_STOP_HINT in text  # "... time limit"
