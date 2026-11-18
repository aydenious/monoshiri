"""The shared eviction pass: TTL drop first, size drop only if still over budget."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.etl_pipeline.cache.eviction.sweep import evict_expired_then_over_budget
from app.etl_pipeline.cache.schemas import EvictionCandidate

pytestmark = pytest.mark.unit


def _candidate(id_: int, size_bytes: int) -> EvictionCandidate:
    return EvictionCandidate(
        id=id_,
        storage_key=f"k/{id_}",
        size_bytes=size_bytes,
        last_used_at=datetime(2026, 1, 1, tzinfo=UTC),
        times_reused=0,
    )


class _Index:
    def __init__(self, expired, coldest, total):
        self.expired, self.coldest, self.total = expired, coldest, total
        self.deleted: list[list[int]] = []

    async def select_expired(self, *, cutoff, limit):
        return self.expired

    async def select_coldest(self, *, limit):
        return self.coldest

    async def total_size_bytes(self):
        return self.total

    async def delete_by_ids(self, ids):
        self.deleted.append(ids)


class _Store:
    def __init__(self, failing=()):
        self.deleted: list[str] = []
        self.failing = failing

    async def delete(self, key):
        if key in self.failing:
            raise RuntimeError("blob gone")
        self.deleted.append(key)


_SETTINGS = SimpleNamespace(ttl_days=30, max_total_bytes=100, eviction_batch=10)


async def test_ttl_then_size_phase_each_report_and_delete():
    index = _Index([_candidate(1, 5)], [_candidate(2, 120), _candidate(3, 90)], 300)
    store, seen = _Store(), []

    await evict_expired_then_over_budget(
        index, store, _SETTINGS, lambda n, phase: seen.append((n, phase))
    )

    assert index.deleted == [[1], [2, 3]]
    assert store.deleted == ["k/1", "k/2", "k/3"]
    assert seen == [(1, "ttl"), (2, "size")]


async def test_blob_delete_failure_still_drops_the_index_row():
    index = _Index([_candidate(1, 5)], [], 0)

    await evict_expired_then_over_budget(
        index, _Store(failing={"k/1"}), _SETTINGS, lambda n, phase: None
    )

    assert index.deleted == [[1]]
