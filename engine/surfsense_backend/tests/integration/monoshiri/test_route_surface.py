"""Guard the HTTP route surface of the Monoshiri fork.

``REQUIRED_ROUTES`` is the API the web UI and the iOS app depend on.
``FORBIDDEN_PREFIXES`` grows as phase-2 tasks strip features.
"""

from __future__ import annotations

import pytest

from app.app import app

pytestmark = pytest.mark.integration

FORBIDDEN_PREFIXES: list[str] = [
    "/api/v1/gateway",  # Task 5: messaging gateways (slack/discord/telegram/whatsapp)
    "/api/v1/stripe",  # Task 6: Stripe checkout, webhook, auto-reload, purchases
    "/api/v1/license",  # Task 6: offline desktop licence files
    "/api/v1/incentive-tasks",  # Task 6: earn-free-pages incentives
    "/api/v1/search-source-connectors",  # Task 7: connector CRUD / indexing / MCP connectors
    "/api/v1/connectors",  # Task 7: connector folder pickers, Luma, MCP connectors
    "/api/v1/auth/google/drive",  # Task 7: connector OAuth (login stays under /auth/google/authorize)
    "/api/v1/auth/google/gmail",
    "/api/v1/auth/google/calendar",
    "/api/v1/auth/airtable",
    "/api/v1/auth/clickup",
    "/api/v1/auth/confluence",
    "/api/v1/auth/discord",
    "/api/v1/auth/dropbox",
    "/api/v1/auth/jira",
    "/api/v1/auth/linear",
    "/api/v1/auth/notion",
    "/api/v1/auth/onedrive",
    "/api/v1/auth/slack",
    "/api/v1/auth/teams",
    "/api/v1/auth/mcp",  # Task 7: MCP OAuth
    "/api/v1/auth/composio",  # Task 7: Composio OAuth
    "/api/v1/composio",  # Task 7: Composio toolkits
    "/api/v1/obsidian",  # Task 7: Obsidian plugin API
    "/api/v1/webhooks/circleback",  # Task 7: Circleback webhooks
    "/api/v1/github",  # Task 7: GitHub repository listing
    "/api/v1/discord",  # Task 7: Discord channel pickers
    "/api/v1/slack",  # Task 7: Slack channel pickers
    "/api/v1/public",  # Task 8: public share links, clone, anonymous (no-login) chat
    "/api/v1/threads/{thread_id}/snapshots",  # Task 8: chat snapshot create/list/delete
    "/api/v1/workspaces/{workspace_id}/snapshots",  # Task 8: workspace snapshot list
    "/api/v1/youtube",  # Task 8b: playlist resolver (its web form went in 7b)
    # Task 8b: API Playground. Only GET .../scrapers/runs/{run_id} stays (run citations in chat).
    "/api/v1/workspaces/{workspace_id}/scrapers/capabilities",
    "/api/v1/workspaces/{workspace_id}/scrapers/runs/{run_id}/",  # events, cancel
    "/api/v1/workspaces/{workspace_id}/scrapers/amazon",
    "/api/v1/workspaces/{workspace_id}/scrapers/walmart",
    "/api/v1/workspaces/{workspace_id}/scrapers/instagram",
    "/api/v1/workspaces/{workspace_id}/scrapers/tiktok",
    "/api/v1/workspaces/{workspace_id}/scrapers/indeed",
    "/api/v1/workspaces/{workspace_id}/scrapers/google_maps",
    "/api/v1/workspaces/{workspace_id}/scrapers/reddit",
    "/api/v1/workspaces/{workspace_id}/scrapers/google_search",  # chat reaches it through the agent door
    "/api/v1/workspaces/{workspace_id}/scrapers/web",
    "/api/v1/workspaces/{workspace_id}/scrapers/youtube",
    # Task 9: team management. Roles, member management and workspace invite
    # links are gone; the member list, my-access and /permissions stay.
    "/api/v1/workspaces/{workspace_id}/roles",
    "/api/v1/workspaces/{workspace_id}/invites",
    "/api/v1/workspaces/{workspace_id}/members/",  # update/remove/leave
    "/api/v1/invites",  # invite info + accept
]

REQUIRED_ROUTES: list[tuple[str, str]] = [
    # auth / session / health
    ("POST", "/auth/jwt/login"),
    ("POST", "/auth/jwt/token"),  # native clients: tokens in the body
    ("GET", "/users/me"),
    ("GET", "/health"),
    ("GET", "/verify-token"),
    ("GET", "/zero/context"),
    # workspaces + export
    ("GET", "/api/v1/workspaces"),
    ("POST", "/api/v1/workspaces"),
    ("GET", "/api/v1/workspaces/{workspace_id}/export"),
    # documents
    ("GET", "/api/v1/documents"),
    ("POST", "/api/v1/documents/fileupload"),
    ("POST", "/api/v1/documents/url"),
    # chat threads + streaming
    ("GET", "/api/v1/threads"),
    ("POST", "/api/v1/threads"),
    ("GET", "/api/v1/threads/{thread_id}/messages"),
    ("POST", "/api/v1/new_chat"),
    # LLM config
    ("GET", "/api/v1/global-llm-config-status"),
    ("GET", "/api/v1/model-connections"),
    ("GET", "/api/v1/global-model-connections"),
    # admin user management (superuser only)
    ("GET", "/api/v1/admin/users"),
    ("POST", "/api/v1/admin/invites"),
    ("GET", "/api/v1/admin/invites"),
    ("DELETE", "/api/v1/admin/invites/{jti}"),
    ("POST", "/api/v1/admin/users/{user_id}/deactivate"),
    ("POST", "/api/v1/admin/users/{user_id}/activate"),
    ("DELETE", "/api/v1/admin/users/{user_id}"),
    # chat run citations (YouTube / web / search runs) open through this
    ("GET", "/api/v1/workspaces/{workspace_id}/scrapers/runs/{run_id}"),
]


def _routes() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", None) or ()
    }


def test_required_routes_present() -> None:
    missing = sorted(set(REQUIRED_ROUTES) - _routes())
    assert not missing, f"required routes missing: {missing}"


def test_forbidden_prefixes_absent() -> None:
    offenders = sorted(
        path
        for _, path in _routes()
        if any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
    )
    assert not offenders, f"forbidden routes still mounted: {offenders}"
