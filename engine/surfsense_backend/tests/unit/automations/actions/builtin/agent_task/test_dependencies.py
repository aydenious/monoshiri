"""Lock how ``build_dependencies`` resolves an automation's chat model.

Automations resolve their LLM from the *captured* ``chat_model_id`` snapshot (so
runs are insulated from later chat/workspace model changes). When no snapshot is
present, resolution falls back to the live workspace.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import app.automations.actions.builtin.agent_task.dependencies as deps_mod
from app.automations.actions.builtin.agent_task.dependencies import (
    DependencyError,
    build_dependencies,
)

pytestmark = pytest.mark.unit


class _FakeSession:
    """Minimal async session whose ``get`` returns a preset workspace."""

    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace

    async def get(self, _model: Any, _pk: int) -> Any:
        return self._workspace


@pytest.fixture
def patched_side_effects(monkeypatch: pytest.MonkeyPatch):
    """Stub the connector setup so only the model-resolution logic runs."""

    async def _fake_setup(_session, *, workspace_id):
        return SimpleNamespace(name="connector")

    monkeypatch.setattr(deps_mod, "setup_connector_service", _fake_setup)
    return None


async def test_build_dependencies_resolves_captured_chat_model_id(
    monkeypatch: pytest.MonkeyPatch, patched_side_effects
) -> None:
    """The bundle loads with the *captured* ``chat_model_id``, not the live workspace."""
    captured: dict[str, Any] = {}

    async def _fake_load(_session, *, config_id, workspace_id):
        captured["config_id"] = config_id
        captured["workspace_id"] = workspace_id
        return (SimpleNamespace(name="llm"), SimpleNamespace(name="agent_config"), None)

    monkeypatch.setattr(deps_mod, "load_llm_bundle", _fake_load)

    # A different value on the live workspace proves we ignore it when a
    # snapshot is supplied.
    workspace = SimpleNamespace(chat_model_id=-99)
    result = await build_dependencies(
        session=_FakeSession(workspace),
        workspace_id=42,
        chat_model_id=-7,
    )

    assert captured == {"config_id": -7, "workspace_id": 42}
    assert result.llm.name == "llm"
    assert result.connector_service.name == "connector"


async def test_build_dependencies_falls_back_to_workspace(
    monkeypatch: pytest.MonkeyPatch, patched_side_effects
) -> None:
    """With no captured snapshot, resolve the live workspace."""
    captured: dict[str, Any] = {}

    async def _fake_load(_session, *, config_id, workspace_id):
        captured["config_id"] = config_id
        return (SimpleNamespace(name="llm"), SimpleNamespace(name="agent_config"), None)

    monkeypatch.setattr(deps_mod, "load_llm_bundle", _fake_load)

    workspace = SimpleNamespace(chat_model_id=-7)
    result = await build_dependencies(session=_FakeSession(workspace), workspace_id=42)

    assert captured == {"config_id": -7}
    assert result.llm.name == "llm"


async def test_build_dependencies_raises_when_workspace_missing(
    patched_side_effects,
) -> None:
    """A missing workspace (fallback path) surfaces as a ``DependencyError``."""
    with pytest.raises(DependencyError):
        await build_dependencies(session=_FakeSession(None), workspace_id=999)
