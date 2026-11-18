"""Tripwire: team management (roles, members, invites) is gone and must stay gone.

Monoshiri is single-user. Nobody joins a workspace, so custom roles, member
management and workspace invite links were removed with their routes and
schemas. The read-only endpoints the UI still reads stay, and so do the
*sign-up* invites (``/admin/invites``, ``/auth/register``), which are a
different feature: they let the owner create an account, not join a workspace.
"""

from __future__ import annotations

import pytest

from app.app import app

pytestmark = pytest.mark.unit

REMOVED_PATHS = [
    # custom roles
    ("POST", "/api/v1/workspaces/{workspace_id}/roles"),
    ("GET", "/api/v1/workspaces/{workspace_id}/roles"),
    ("GET", "/api/v1/workspaces/{workspace_id}/roles/{role_id}"),
    ("PUT", "/api/v1/workspaces/{workspace_id}/roles/{role_id}"),
    ("DELETE", "/api/v1/workspaces/{workspace_id}/roles/{role_id}"),
    # member management (the member list itself stays)
    ("PUT", "/api/v1/workspaces/{workspace_id}/members/{membership_id}"),
    ("DELETE", "/api/v1/workspaces/{workspace_id}/members/{membership_id}"),
    ("DELETE", "/api/v1/workspaces/{workspace_id}/members/me"),
    # workspace invite links
    ("POST", "/api/v1/workspaces/{workspace_id}/invites"),
    ("GET", "/api/v1/workspaces/{workspace_id}/invites"),
    ("PUT", "/api/v1/workspaces/{workspace_id}/invites/{invite_id}"),
    ("DELETE", "/api/v1/workspaces/{workspace_id}/invites/{invite_id}"),
    ("GET", "/api/v1/invites/{invite_code}/info"),
    ("POST", "/api/v1/invites/accept"),
]

KEPT_PATHS = [
    # read-only endpoints the web UI still calls
    ("GET", "/api/v1/workspaces/{workspace_id}/members"),
    ("GET", "/api/v1/workspaces/{workspace_id}/my-access"),
    ("GET", "/api/v1/permissions"),
    # sign-up invites and sign-up itself: a different feature, still needed
    ("POST", "/api/v1/admin/invites"),
    ("GET", "/api/v1/admin/invites"),
    ("POST", "/auth/register"),
]


def _paths() -> dict[str, set[str]]:
    return {
        path: {method.lower() for method in operations}
        for path, operations in app.openapi()["paths"].items()
    }


@pytest.mark.parametrize(("method", "path"), REMOVED_PATHS)
def test_removed_team_path_is_gone(method: str, path: str) -> None:
    assert method.lower() not in _paths().get(path, set()), f"{method} {path} is back"


@pytest.mark.parametrize(("method", "path"), KEPT_PATHS)
def test_kept_path_is_present(method: str, path: str) -> None:
    assert method.lower() in _paths().get(path, set()), f"{method} {path} went missing"
