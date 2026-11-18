"""The transcript-drafting task against a real database.

Drafting is the expensive LLM step, so it runs under ``recorded_llm_call``.
Once the draft succeeds the transcript is stored and rendering starts
immediately (DRAFTING -> RENDERING, render task enqueued — the brief gate is
the only approval). The DB, service, and transcript persistence run for real;
only the true externals are faked — the usage recorder and the generation
graph (the LLM).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.podcasts.persistence import PodcastStatus
from app.podcasts.service import read_transcript
from app.podcasts.tasks import draft

from .conftest import build_transcript

pytestmark = pytest.mark.integration


def _wire_recorder(monkeypatch, *, recorded_llm_call, transcript=None) -> None:
    """Replace the usage recorder + LLM externals the draft body reaches for."""

    async def _owner(_session, _workspace_id):
        return uuid4()

    async def _ainvoke(_state, config=None):
        return {"transcript": transcript}

    monkeypatch.setattr(draft, "resolve_workspace_owner", _owner)
    monkeypatch.setattr(draft, "recorded_llm_call", recorded_llm_call)
    monkeypatch.setattr(draft, "transcript_graph", SimpleNamespace(ainvoke=_ainvoke))


async def test_successful_draft_stores_transcript_and_starts_rendering(
    monkeypatch, db_workspace, make_podcast, bind_task_session, captured_tasks
):
    podcast = await make_podcast(
        workspace_id=db_workspace.id, status=PodcastStatus.DRAFTING
    )

    @asynccontextmanager
    async def _ok(**_kwargs):
        yield SimpleNamespace()

    _wire_recorder(monkeypatch, recorded_llm_call=_ok, transcript=build_transcript())

    result = await draft._draft_transcript(podcast.id, db_workspace.id)

    assert result["status"] == "rendering"
    assert podcast.status == PodcastStatus.RENDERING
    assert read_transcript(podcast) is not None
    assert captured_tasks.render == [((podcast.id,), {})]
