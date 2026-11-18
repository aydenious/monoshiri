"""Task 8b: the API Playground and the scraper capabilities (amazon, walmart, instagram,
tiktok, indeed, google_maps, reddit) are gone. Chat keeps web search (google_search), the
web crawler and YouTube, plus the knowledge base and deliverables sub-agents."""

import importlib.util

import pytest

pytestmark = pytest.mark.unit

REMOVED = ("amazon", "walmart", "instagram", "tiktok", "indeed", "google_maps", "reddit")
KEPT = ("google_search", "web_crawler", "youtube", "knowledge_base", "deliverables")


def test_removed_subagents_not_registered():
    from app.agents.chat.multi_agent_chat.subagents.registry import (
        SUBAGENT_BUILDERS_BY_NAME,
    )

    assert not set(REMOVED) & set(SUBAGENT_BUILDERS_BY_NAME)


def test_kept_subagents_registered():
    from app.agents.chat.multi_agent_chat.subagents.registry import (
        SUBAGENT_BUILDERS_BY_NAME,
    )

    assert set(KEPT) <= set(SUBAGENT_BUILDERS_BY_NAME)


def test_removed_capabilities_not_registered():
    import app.routes  # noqa: F401  (registers the kept capabilities)
    from app.capabilities.core.store import all_capabilities

    names = {c.name for c in all_capabilities()}
    assert not {n for n in names if n.split(".")[0] in REMOVED}
    assert {"youtube.scrape", "web.crawl", "google_search.scrape"} <= names


@pytest.mark.parametrize("platform", REMOVED)
def test_removed_packages_gone(platform):
    assert importlib.util.find_spec(f"app.capabilities.{platform}") is None
    proprietary = "indeed_jobs" if platform == "indeed" else platform
    assert importlib.util.find_spec(f"app.proprietary.platforms.{proprietary}") is None


def test_only_run_detail_route_remains():
    from app.app import app

    paths = {getattr(r, "path", "") for r in app.routes}
    scraper_paths = {p for p in paths if "/scrapers" in p}
    assert scraper_paths == {"/api/v1/workspaces/{workspace_id}/scrapers/runs/{run_id}"}
