"""Unit tests for the video-presentation Celery task's usage recording.

The graph runs inside one ``recorded_llm_call`` bound to the workspace owner,
and a run whose owner cannot be resolved ends FAILED without invoking the
graph.
"""

from __future__ import annotations

import contextlib
import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeExecResult:
    def __init__(self, obj):
        self._obj = obj

    def scalars(self):
        return self

    def first(self):
        return self._obj

    def filter(self, *_args, **_kwargs):
        return self


class _FakeSession:
    def __init__(self, video):
        self._video = video
        self.commit_count = 0

    async def execute(self, _stmt):
        return _FakeExecResult(self._video)

    async def commit(self):
        self.commit_count += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeSessionMaker:
    def __init__(self, session: _FakeSession):
        self._session = session

    def __call__(self):
        return self._session


def _make_video(
    video_id: int = 11, thread_id: int = 99, workspace_id: int = 777
) -> SimpleNamespace:
    return SimpleNamespace(
        id=video_id,
        title="Test Presentation",
        thread_id=thread_id,
        workspace_id=workspace_id,
        artifact_id=None,
        status=None,
        slides=None,
        scene_codes=None,
    )


_CALL_LOG: list[dict[str, Any]] = []


@contextlib.asynccontextmanager
async def _ok_recorded_call(**kwargs):
    _CALL_LOG.append(kwargs)
    yield SimpleNamespace()


@pytest.fixture(autouse=True)
def _reset_call_log():
    _CALL_LOG.clear()
    yield
    _CALL_LOG.clear()


@pytest.mark.asyncio
async def test_graph_runs_under_one_usage_recording(monkeypatch):
    from app.db import VideoPresentationStatus
    from app.tasks.celery_tasks import video_presentation_tasks

    video = _make_video(video_id=11, thread_id=99)
    session = _FakeSession(video)
    monkeypatch.setattr(
        video_presentation_tasks,
        "get_celery_session_maker",
        lambda: _FakeSessionMaker(session),
    )

    user_id = uuid4()

    async def _fake_owner(_session, workspace_id):
        assert workspace_id == 777
        return user_id

    monkeypatch.setattr(
        video_presentation_tasks,
        "resolve_workspace_owner",
        _fake_owner,
    )
    monkeypatch.setattr(
        video_presentation_tasks, "recorded_llm_call", _ok_recorded_call
    )

    async def _fake_graph_invoke(state, config):
        return {"slides": [], "slide_audio_results": [], "slide_scene_codes": []}

    monkeypatch.setattr(
        video_presentation_tasks.video_presentation_graph,
        "ainvoke",
        _fake_graph_invoke,
    )

    result = await video_presentation_tasks._generate_video_presentation(
        video_presentation_id=11,
        source_content="content",
        workspace_id=777,
        user_prompt=None,
    )

    assert result["status"] == "ready"
    assert result["video_presentation_id"] == 11
    assert video.status == VideoPresentationStatus.READY

    assert len(_CALL_LOG) == 1
    call = _CALL_LOG[0]
    assert call["user_id"] == user_id
    assert call["workspace_id"] == 777
    assert call["usage_type"] == "video_presentation_generation"
    # Background artifact audit rows intentionally omit the TokenUsage.thread_id
    # FK to avoid coupling Celery audit commits to an active chat transaction.
    assert "thread_id" not in call
    assert call["call_details"] == {
        "video_presentation_id": 11,
        "title": "Test Presentation",
        "thread_id": 99,
    }
    assert callable(call["session_factory"])


@pytest.mark.asyncio
async def test_resolver_failure_marks_video_failed(monkeypatch):
    from app.db import VideoPresentationStatus
    from app.tasks.celery_tasks import video_presentation_tasks

    video = _make_video(video_id=13)
    session = _FakeSession(video)
    monkeypatch.setattr(
        video_presentation_tasks,
        "get_celery_session_maker",
        lambda: _FakeSessionMaker(session),
    )

    async def _failing_resolver(_session, _workspace_id):
        raise ValueError("Workspace 777 not found")

    monkeypatch.setattr(
        video_presentation_tasks,
        "resolve_workspace_owner",
        _failing_resolver,
    )

    graph_invoked = []

    async def _fake_graph_invoke(state, config):
        graph_invoked.append(True)
        return {}

    monkeypatch.setattr(
        video_presentation_tasks.video_presentation_graph,
        "ainvoke",
        _fake_graph_invoke,
    )

    result = await video_presentation_tasks._generate_video_presentation(
        video_presentation_id=13,
        source_content="content",
        workspace_id=777,
        user_prompt=None,
    )

    assert result == {
        "status": "failed",
        "video_presentation_id": 13,
        "reason": "owner_resolution_failed",
    }
    assert video.status == VideoPresentationStatus.FAILED
    assert graph_invoked == []


# ---------------------------------------------------------------------------
# _mark_video_presentation_failed: only a DB fault is contained
# ---------------------------------------------------------------------------


class _BrokenSession:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def execute(self, _stmt):
        raise self._error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_marking_failed_tolerates_a_db_error(monkeypatch, caplog):
    from app.tasks.celery_tasks import video_presentation_tasks

    error = OperationalError("UPDATE", None, Exception("connection refused"))
    monkeypatch.setattr(
        video_presentation_tasks,
        "get_celery_session_maker",
        lambda: lambda: _BrokenSession(error),
    )

    with caplog.at_level(logging.ERROR, logger=video_presentation_tasks.logger.name):
        await video_presentation_tasks._mark_video_presentation_failed(11, error="x")

    assert "Failed to mark video presentation as failed" in caplog.text


@pytest.mark.asyncio
async def test_marking_failed_does_not_swallow_a_bug(monkeypatch):
    from app.tasks.celery_tasks import video_presentation_tasks

    monkeypatch.setattr(
        video_presentation_tasks,
        "get_celery_session_maker",
        lambda: lambda: _BrokenSession(ValueError("bad row")),
    )

    with pytest.raises(ValueError):
        await video_presentation_tasks._mark_video_presentation_failed(11, error="x")
