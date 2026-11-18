"""Transcript-drafting task: DRAFTING -> RENDERING.

The expensive, LLM-heavy step, so it runs under ``recorded_llm_call``. The API
has already moved the row to DRAFTING and stored the approved brief; this task
drafts the long-form transcript and chains straight into the render — the brief
gate is the only approval in the lifecycle.
"""

from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.podcasts.generation.transcript.graph import graph as transcript_graph
from app.podcasts.generation.transcript.state import TranscriptState
from app.podcasts.persistence import PodcastRepository
from app.podcasts.service import PodcastService, read_spec
from app.services.llm_usage import recorded_llm_call, resolve_workspace_owner
from app.tasks.celery_tasks import get_celery_session_maker, run_async_celery_task

from .render import render_audio_task
from .runtime import mark_failed, usage_session

logger = logging.getLogger(__name__)


@celery_app.task(name="podcast.draft_transcript", bind=True)
def draft_transcript_task(self, podcast_id: int, workspace_id: int) -> dict:
    try:
        return run_async_celery_task(
            lambda: _draft_transcript(podcast_id, workspace_id)
        )
    except Exception as exc:
        logger.error("Podcast %s drafting failed: %s", podcast_id, exc)
        message = str(exc)
        run_async_celery_task(lambda: mark_failed(podcast_id, message))
        return {"status": "failed", "podcast_id": podcast_id}


async def _draft_transcript(podcast_id: int, workspace_id: int) -> dict:
    async with get_celery_session_maker()() as session:
        repo = PodcastRepository(session)
        service = PodcastService(session)
        podcast = await repo.get(podcast_id)
        if podcast is None:
            raise ValueError(f"podcast {podcast_id} not found")

        spec = read_spec(podcast)
        if spec is None:
            raise ValueError(f"podcast {podcast_id} has no approved brief")

        owner_id = await resolve_workspace_owner(session, workspace_id)

        state = TranscriptState(
            db_session=session, source_content=podcast.source_content or ""
        )
        config = {
            "configurable": {
                "workspace_id": workspace_id,
                "spec": spec,
                "focus": spec.focus,
            }
        }

        async with recorded_llm_call(
            user_id=owner_id,
            workspace_id=workspace_id,
            usage_type="podcast_generation",
            call_details={"podcast_id": podcast_id, "title": podcast.title},
            session_factory=usage_session,
        ):
            result = await transcript_graph.ainvoke(state, config=config)

        await service.attach_transcript(podcast, result["transcript"])
        await session.commit()

    # Enqueue only after the transaction is committed, so the render worker can
    # never pick up a row whose transcript isn't visible yet.
    render_audio_task.delay(podcast_id)
    return {"status": "rendering", "podcast_id": podcast_id}
