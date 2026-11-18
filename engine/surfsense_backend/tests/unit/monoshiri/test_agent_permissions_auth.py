"""Monoshiri: agent permission routes must hand check_permission the AuthContext.

Upstream 2.1.0 passed ``auth.user`` (a User) where ``check_permission`` expects an
AuthContext, so every /agent/permissions/rules call 500'd with
``'User' object has no attribute 'user'``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routes import agent_permissions_route as route


@pytest.mark.asyncio
async def test_membership_check_receives_auth_context(monkeypatch):
    seen = {}

    async def fake_check_permission(session, auth, workspace_id, permission, msg):
        seen["auth"] = auth
        raise route.HTTPException(status_code=403, detail="stop here")

    monkeypatch.setattr(route, "check_permission", fake_check_permission)
    monkeypatch.setattr(route, "_flag_guard", lambda: None)

    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(id=8))
    auth = SimpleNamespace(user=SimpleNamespace(id="u1"))

    with pytest.raises(route.HTTPException) as exc:
        await route.list_rules(workspace_id=8, session=session, auth=auth)

    assert exc.value.status_code == 403
    assert seen["auth"] is auth
