"""The eviction pass shared by the parse cache and the embedding cache."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.etl_pipeline.cache.eviction.policy import select_over_budget
from app.etl_pipeline.cache.schemas import EvictionCandidate


class EvictionSettings(Protocol):
    ttl_days: int
    max_total_bytes: int
    eviction_batch: int


async def evict_expired_then_over_budget(
    index,
    store,
    settings: EvictionSettings,
    on_evicted: Callable[[int, str], None],
) -> None:
    """Expire stale entries, then shed the coldest overflow only if still over budget.

    ``on_evicted(count, phase)`` is called after each non-empty drop, with phase
    ``"ttl"`` or ``"size"``.
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.ttl_days)
    expired = await index.select_expired(cutoff=cutoff, limit=settings.eviction_batch)
    await _drop(index, store, expired, "ttl", on_evicted)

    total = await index.total_size_bytes()
    if total > settings.max_total_bytes:
        coldest = await index.select_coldest(limit=settings.eviction_batch)
        over_budget = select_over_budget(
            coldest,
            current_total_bytes=total,
            max_total_bytes=settings.max_total_bytes,
        )
        await _drop(index, store, over_budget, "size", on_evicted)


async def _drop(
    index,
    store,
    candidates: Sequence[EvictionCandidate],
    phase: str,
    on_evicted: Callable[[int, str], None],
) -> None:
    if not candidates:
        return
    for candidate in candidates:
        # Drop the index row even if the blob delete fails (orphan blob is harmless).
        with contextlib.suppress(Exception):
            await store.delete(candidate.storage_key)
    await index.delete_by_ids([candidate.id for candidate in candidates])
    on_evicted(len(candidates), phase)
