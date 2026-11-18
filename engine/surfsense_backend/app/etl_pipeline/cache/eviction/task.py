"""Celery task that prunes the parse cache by TTL, then by size budget."""

from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.etl_pipeline.cache.eviction.sweep import evict_expired_then_over_budget
from app.etl_pipeline.cache.persistence import CachedParseRepository
from app.etl_pipeline.cache.settings import load_etl_cache_settings
from app.etl_pipeline.cache.storage import MarkdownCacheStore
from app.observability.domains import etl
from app.tasks.celery_tasks import get_celery_session_maker, run_async_celery_task

logger = logging.getLogger(__name__)


@celery_app.task(name="evict_etl_cache")
def evict_etl_cache_task():
    return run_async_celery_task(_evict)


async def _evict() -> None:
    settings = load_etl_cache_settings()
    if not settings.enabled:
        return

    async with get_celery_session_maker()() as session:
        await evict_expired_then_over_budget(
            CachedParseRepository(session),
            MarkdownCacheStore(),
            settings,
            _record,
        )


def _record(count: int, phase: str) -> None:
    etl.record_etl_cache_eviction(count, phase=phase)
    logger.info("Evicted %d cached parses (%s)", count, phase)
