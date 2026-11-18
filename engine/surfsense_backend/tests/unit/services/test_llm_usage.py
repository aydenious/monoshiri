"""Unit tests for ``recorded_llm_call`` and ``resolve_workspace_owner``.

The recorder's one job: write exactly one ``TokenUsage`` row per call, with the
tokens and provider cost the accumulator captured, and never let the audit
insert block the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


class _FakeSession:
    """Minimal AsyncSession stub — record commits for assertion."""

    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


_SESSIONS_USED: list[_FakeSession] = []


@contextlib.asynccontextmanager
async def _fake_shielded_session():
    session = _FakeSession()
    _SESSIONS_USED.append(session)
    yield session


def _patch_recorder(monkeypatch) -> list[dict[str, Any]]:
    """Wire a fake audit session and capture the ``record_token_usage`` kwargs."""
    _SESSIONS_USED.clear()
    record_calls: list[dict[str, Any]] = []

    async def _fake_record(_session, **kwargs):
        record_calls.append(kwargs)
        return object()

    monkeypatch.setattr(
        "app.services.llm_usage.shielded_async_session",
        _fake_shielded_session,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.llm_usage.record_token_usage",
        _fake_record,
        raising=False,
    )
    return record_calls


@pytest.mark.asyncio
async def test_call_writes_one_usage_row(monkeypatch):
    from app.services.llm_usage import recorded_llm_call

    record_calls = _patch_recorder(monkeypatch)
    user_id = uuid4()

    async with recorded_llm_call(
        user_id=user_id,
        workspace_id=42,
        usage_type="image_generation",
        call_details={"prompt": "an owl"},
    ) as acc:
        acc.add(
            model="openai/gpt-image-1",
            prompt_tokens=11,
            completion_tokens=0,
            total_tokens=11,
            cost_micros=37_000,
            call_kind="image_generation",
        )

    assert len(record_calls) == 1
    row = record_calls[0]
    assert row["usage_type"] == "image_generation"
    assert row["workspace_id"] == 42
    assert row["user_id"] == user_id
    assert row["prompt_tokens"] == 11
    assert row["total_tokens"] == 11
    assert row["cost_micros"] == 37_000
    assert row["call_details"] == {"prompt": "an owl"}
    assert any(session.committed for session in _SESSIONS_USED)


@pytest.mark.asyncio
async def test_call_records_when_the_body_raises(monkeypatch):
    from app.services.llm_usage import recorded_llm_call

    record_calls = _patch_recorder(monkeypatch)

    class _ProviderError(Exception):
        pass

    with pytest.raises(_ProviderError):
        async with recorded_llm_call(
            user_id=uuid4(),
            workspace_id=42,
            usage_type="vision_extraction",
        ) as acc:
            acc.add(
                model="openai/gpt-4o",
                prompt_tokens=5,
                completion_tokens=5,
                total_tokens=10,
                cost_micros=12,
                call_kind="vision",
            )
            raise _ProviderError("provider 503")

    assert len(record_calls) == 1
    assert record_calls[0]["total_tokens"] == 10


@pytest.mark.asyncio
async def test_background_artifact_rows_omit_thread_id(monkeypatch):
    """Podcast/video audit rows leave the TokenUsage.thread_id FK NULL so a
    Celery audit commit is never coupled to an active chat transaction."""
    from app.services.llm_usage import recorded_llm_call

    record_calls = _patch_recorder(monkeypatch)

    async with recorded_llm_call(
        user_id=uuid4(),
        workspace_id=42,
        usage_type="podcast_generation",
        thread_id=99,
    ):
        pass

    assert record_calls[0]["thread_id"] is None


@pytest.mark.asyncio
async def test_chat_scoped_usage_keeps_thread_id(monkeypatch):
    from app.services.llm_usage import recorded_llm_call

    record_calls = _patch_recorder(monkeypatch)

    async with recorded_llm_call(
        user_id=uuid4(),
        workspace_id=42,
        usage_type="queued_deliverable_generation",
        thread_id=99,
        message_id=7,
    ):
        pass

    assert record_calls[0]["thread_id"] == 99
    assert record_calls[0]["message_id"] == 7


@pytest.mark.asyncio
async def test_audit_failure_is_best_effort(monkeypatch):
    from app.services.llm_usage import recorded_llm_call

    _patch_recorder(monkeypatch)

    async def _failing_record(_session, **_kwargs):
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(
        "app.services.llm_usage.record_token_usage",
        _failing_record,
        raising=False,
    )

    async with recorded_llm_call(
        user_id=uuid4(),
        workspace_id=42,
        usage_type="image_generation",
        audit_timeout_seconds=0.01,
    ):
        pass


@pytest.mark.asyncio
async def test_hanging_audit_commit_is_bounded(monkeypatch):
    from app.services.llm_usage import recorded_llm_call

    record_calls = _patch_recorder(monkeypatch)

    class _HangingCommitSession(_FakeSession):
        async def commit(self) -> None:
            await asyncio.sleep(60)

    @contextlib.asynccontextmanager
    async def _hanging_session_factory():
        session = _HangingCommitSession()
        _SESSIONS_USED.append(session)
        yield session

    async with recorded_llm_call(
        user_id=uuid4(),
        workspace_id=42,
        usage_type="video_presentation_generation",
        session_factory=_hanging_session_factory,
        audit_timeout_seconds=0.01,
    ):
        pass

    # The row was staged; only the commit hung and the wait_for cut it short.
    assert len(record_calls) == 1


class _OwnerSession:
    def __init__(self, owner) -> None:
        self._owner = owner

    async def execute(self, _stmt):
        owner = self._owner
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: owner))


@pytest.mark.asyncio
async def test_resolve_workspace_owner_returns_the_owner():
    from app.services.llm_usage import resolve_workspace_owner

    owner = uuid4()

    assert await resolve_workspace_owner(_OwnerSession(owner), 42) == owner


@pytest.mark.asyncio
async def test_resolve_workspace_owner_missing_workspace_raises():
    from app.services.llm_usage import resolve_workspace_owner

    with pytest.raises(ValueError, match="Workspace 42 not found"):
        await resolve_workspace_owner(_OwnerSession(None), 42)
