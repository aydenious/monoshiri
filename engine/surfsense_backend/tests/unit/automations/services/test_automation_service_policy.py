"""Lock how ``AutomationService`` captures and reports automation models.

Creation snapshots the workspace's model prefs when the payload carries none
and keeps an explicit per-automation selection as provided; no model slot is
rejected. ``model_eligibility`` keeps authorizing and answers "allowed".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import app.automations.services.automation as automation_mod
from app.auth.context import AuthContext
from app.automations.schemas.api import AutomationCreate, AutomationUpdate
from app.automations.schemas.definition.envelope import (
    AutomationDefinition,
    AutomationModels,
)
from app.automations.schemas.definition.plan_step import PlanStep
from app.automations.services.automation import AutomationService

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace
        self.added: list[Any] = []
        self.commits = 0

    async def get(self, _model: Any, _pk: int) -> Any:
        return self._workspace

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


def _service(workspace: Any) -> AutomationService:
    return AutomationService(
        session=_FakeSession(workspace),
        auth=AuthContext.session(SimpleNamespace(id="u-1")),
    )


def _definition(**kwargs: Any) -> AutomationDefinition:
    return AutomationDefinition(
        name="A",
        plan=[PlanStep(step_id="s1", action="agent_task")],
        **kwargs,
    )


async def test_create_injects_captured_models_from_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create() snapshots the workspace's model prefs onto the definition."""

    async def _noop_authorize(self, *_a, **_k):
        return None

    monkeypatch.setattr(AutomationService, "_authorize", _noop_authorize)

    async def _return_added(self, _aid):
        return self.session.added[-1]

    monkeypatch.setattr(AutomationService, "_get_with_triggers_or_raise", _return_added)

    workspace = SimpleNamespace(
        chat_model_id=-1,
        image_gen_model_id=5,
        vision_model_id=-1,
    )
    service = _service(workspace)
    payload = AutomationCreate(
        workspace_id=1,
        name="A",
        definition=_definition(),
    )

    automation = await service.create(payload)

    assert automation.definition["models"] == {
        "chat_model_id": -1,
        "image_gen_model_id": 5,
        "vision_model_id": -1,
    }


async def test_create_treats_unset_prefs_as_auto_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` workspace prefs are captured as ``0`` (Auto) ids."""

    async def _noop_authorize(self, *_a, **_k):
        return None

    monkeypatch.setattr(AutomationService, "_authorize", _noop_authorize)

    async def _return_added(self, _aid):
        return self.session.added[-1]

    monkeypatch.setattr(AutomationService, "_get_with_triggers_or_raise", _return_added)

    workspace = SimpleNamespace(
        chat_model_id=None,
        image_gen_model_id=None,
        vision_model_id=None,
    )
    service = _service(workspace)
    payload = AutomationCreate(workspace_id=1, name="A", definition=_definition())

    automation = await service.create(payload)

    assert automation.definition["models"] == {
        "chat_model_id": 0,
        "image_gen_model_id": 0,
        "vision_model_id": 0,
    }


async def test_create_honors_selected_models_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the payload carries ``definition.models`` they are kept as given.

    The workspace snapshot path is bypassed entirely.
    """

    async def _noop_authorize(self, *_a, **_k):
        return None

    async def _return_added(self, _aid):
        return self.session.added[-1]

    monkeypatch.setattr(AutomationService, "_authorize", _noop_authorize)
    monkeypatch.setattr(AutomationService, "_get_with_triggers_or_raise", _return_added)

    service = _service(SimpleNamespace(chat_model_id=-99))
    payload = AutomationCreate(
        workspace_id=1,
        name="A",
        definition=_definition(
            models=AutomationModels(
                chat_model_id=-1,
                image_gen_model_id=7,
                vision_model_id=-2,
            )
        ),
    )

    automation = await service.create(payload)

    assert automation.definition["models"] == {
        "chat_model_id": -1,
        "image_gen_model_id": 7,
        "vision_model_id": -2,
    }


async def test_update_preserves_captured_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A definition edit carries over the previously captured ``models``."""
    captured = {
        "chat_model_id": -1,
        "image_gen_model_id": 5,
        "vision_model_id": -1,
    }
    existing = SimpleNamespace(
        workspace_id=1,
        definition={"name": "A", "plan": [], "models": captured},
        version=3,
    )

    async def _noop_authorize(self, *_a, **_k):
        return None

    async def _return_existing(self, _aid):
        return existing

    monkeypatch.setattr(AutomationService, "_authorize", _noop_authorize)
    monkeypatch.setattr(
        AutomationService, "_get_with_triggers_or_raise", _return_existing
    )

    service = _service(SimpleNamespace())
    # The incoming patch definition has no ``models`` (frontend strips it).
    patch = AutomationUpdate(definition=_definition())

    result = await service.update(7, patch)

    assert result.definition["models"] == captured
    assert result.version == 4


async def test_update_honors_changed_models_when_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A definition edit with a *changed* models block keeps it."""
    existing = SimpleNamespace(
        workspace_id=1,
        definition={
            "name": "A",
            "plan": [],
            "models": {
                "chat_model_id": -1,
                "image_gen_model_id": 5,
                "vision_model_id": -1,
            },
        },
        version=3,
    )

    async def _noop_authorize(self, *_a, **_k):
        return None

    async def _return_existing(self, _aid):
        return existing

    monkeypatch.setattr(AutomationService, "_authorize", _noop_authorize)
    monkeypatch.setattr(
        AutomationService, "_get_with_triggers_or_raise", _return_existing
    )

    service = _service(SimpleNamespace())
    patch = AutomationUpdate(
        definition=_definition(
            models=AutomationModels(
                chat_model_id=-2,
                image_gen_model_id=9,
                vision_model_id=-2,
            )
        )
    )

    result = await service.update(7, patch)

    assert result.definition["models"] == {
        "chat_model_id": -2,
        "image_gen_model_id": 9,
        "vision_model_id": -2,
    }
    assert result.version == 4


async def test_update_keeps_unchanged_models_without_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged models block is kept as-is."""
    captured = {
        "chat_model_id": -1,
        "image_gen_model_id": 5,
        "vision_model_id": -1,
    }
    existing = SimpleNamespace(
        workspace_id=1,
        definition={"name": "A", "plan": [], "models": captured},
        version=3,
    )

    async def _noop_authorize(self, *_a, **_k):
        return None

    async def _return_existing(self, _aid):
        return existing

    monkeypatch.setattr(AutomationService, "_authorize", _noop_authorize)
    monkeypatch.setattr(
        AutomationService, "_get_with_triggers_or_raise", _return_existing
    )

    service = _service(SimpleNamespace())
    patch = AutomationUpdate(
        definition=_definition(models=AutomationModels(**captured))
    )

    result = await service.update(7, patch)

    assert result.definition["models"] == captured
    assert result.version == 4


async def test_model_eligibility_authorizes_and_allows_every_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``model_eligibility`` checks read access, then reports no violation."""
    authorized: dict[str, Any] = {}

    async def _fake_check_permission(_session, _user, ss_id, permission, _msg):
        authorized["workspace_id"] = ss_id
        authorized["permission"] = permission

    monkeypatch.setattr(automation_mod, "check_permission", _fake_check_permission)

    service = _service(SimpleNamespace(chat_model_id=-2, image_gen_model_id=0))
    result = await service.model_eligibility(workspace_id=5)

    assert result == {"allowed": True, "violations": []}
    assert authorized["workspace_id"] == 5
    assert authorized["permission"] == "automations:read"


async def test_model_eligibility_404_when_workspace_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_check_permission(_session, _user, _ss_id, _permission, _msg):
        return None

    monkeypatch.setattr(automation_mod, "check_permission", _fake_check_permission)

    service = _service(None)
    with pytest.raises(HTTPException) as exc_info:
        await service.model_eligibility(workspace_id=999)

    assert exc_info.value.status_code == 404


async def test_create_404_when_workspace_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshotting the model prefs needs the workspace row."""

    async def _noop_authorize(self, *_a, **_k):
        return None

    monkeypatch.setattr(AutomationService, "_authorize", _noop_authorize)

    service = _service(None)
    payload = AutomationCreate(workspace_id=999, name="A", definition=_definition())

    with pytest.raises(HTTPException) as exc_info:
        await service.create(payload)

    assert exc_info.value.status_code == 404
