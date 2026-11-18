"""Unit tests for folder resolution helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


def _session_yielding(scalar_values):
    """AsyncMock session whose ``execute().scalar_one_or_none()`` walks the list."""
    values = iter(scalar_values)

    def make_result(*_args, **_kwargs):
        res = MagicMock()
        res.scalar_one_or_none.return_value = next(values)
        return res

    session = AsyncMock()
    session.execute.side_effect = make_result
    return session


@pytest.mark.asyncio
async def test_resolve_returns_leaf_id_for_existing_chain():
    """Every segment exists -> the deepest folder id, without creating rows."""
    from app.services.folder_service import resolve_folder_id_by_parts

    session = _session_yielding([10, 11, 12])

    result = await resolve_folder_id_by_parts(
        session, workspace_id=1, folder_parts=["GitHub", "owner", "repo"]
    )

    assert result == 12
    assert session.execute.await_count == 3
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_returns_none_when_a_segment_is_missing():
    """A gap anywhere short-circuits to None (folder not indexed yet)."""
    from app.services.folder_service import resolve_folder_id_by_parts

    session = _session_yielding([10, None])

    result = await resolve_folder_id_by_parts(
        session, workspace_id=1, folder_parts=["GitHub", "owner", "repo"]
    )

    assert result is None
    # Stops at the missing segment; never looks up "repo".
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_resolve_returns_none_for_empty_parts():
    from app.services.folder_service import resolve_folder_id_by_parts

    session = AsyncMock()

    result = await resolve_folder_id_by_parts(session, workspace_id=1, folder_parts=[])

    assert result is None
    session.execute.assert_not_called()
