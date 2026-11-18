"""Celery task that prunes the embedding cache by TTL, then by size budget."""

from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.etl_pipeline.cache.eviction.sweep import evict_expired_then_over_budget
from app.indexing_pipeline.cache.persistence import CachedEmbeddingSetRepository
from app.indexing_pipeline.cache.settings import load_embedding_cache_settings
from app.indexing_pipeline.cache.storage import EmbeddingCacheStore
from app.observability.domains import indexing
from app.tasks.celery_tasks import get_celery_session_maker, run_async_celery_task

logger = logging.getLogger(__name__)


@celery_app.task(name="evict_embedding_cache")
def evict_embedding_cache_task():
    return run_async_celery_task(_evict)


async def _evict() -> None:
    settings = load_embedding_cache_settings()
    if not settings.enabled:
        return

    async with get_celery_session_maker()() as session:
        await evict_expired_then_over_budget(
            CachedEmbeddingSetRepository(session),
            EmbeddingCacheStore(),
            settings,
            _record,
        )


def _record(count: int, phase: str) -> None:
    indexing.record_embedding_cache_eviction(count, phase=phase)
    logger.info("Evicted %d cached embedding sets (%s)", count, phase)
