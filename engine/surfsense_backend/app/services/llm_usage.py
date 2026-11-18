"""Token-usage recorder for a single out-of-turn LLM or image call.

``recorded_llm_call`` is the one place image generation, vision extraction,
podcasts, video presentations and queued deliverables record what a call cost:
it scopes a fresh ``TurnTokenAccumulator`` to the ``async with`` body and writes
one ``TokenUsage`` row from it through ``token_tracking_service``. Nothing is
reserved, metered or blocked — usage recording is the whole job.

Two properties callers rely on:

1. **Session isolation.** The audit-row insert runs in its own session
   context, never in the caller's transaction. Route callers use
   ``shielded_async_session()`` by default; Celery callers can provide a
   worker-loop-safe session factory.

2. **ContextVar safety.** The accumulator is scoped via :func:`scoped_turn`
   (which uses ``ContextVar.reset(token)``), so a nested call inside an outer
   chat turn cannot corrupt the chat turn's accumulator.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Workspace, shielded_async_session
from app.services.token_tracking_service import (
    TurnTokenAccumulator,
    record_token_usage,
    scoped_turn,
)

logger = logging.getLogger(__name__)

AUDIT_TIMEOUT_SECONDS = 10.0
BACKGROUND_ARTIFACT_USAGE_TYPES = frozenset(
    {"video_presentation_generation", "podcast_generation"}
)
UsageSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def _rollback_safely(session: AsyncSession) -> None:
    rollback = getattr(session, "rollback", None)
    if rollback is not None:
        with suppress(Exception):
            await rollback()


async def _record_audit_best_effort(
    *,
    session_factory: UsageSessionFactory,
    usage_type: str,
    workspace_id: int,
    user_id: UUID,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost_micros: int,
    model_breakdown: dict[str, Any],
    call_details: dict[str, Any] | None,
    thread_id: int | None,
    message_id: int | None,
    audit_label: str,
    timeout_seconds: float = AUDIT_TIMEOUT_SECONDS,
) -> None:
    """Persist a TokenUsage row without letting audit failure block callers.

    TokenUsage is an audit trail, not a gate: if the insert or commit hangs,
    user-facing artifacts such as videos and podcasts must still be able to
    transition to READY.
    """
    audit_thread_id = (
        None if usage_type in BACKGROUND_ARTIFACT_USAGE_TYPES else thread_id
    )

    async def _persist() -> None:
        logger.info(
            "[llm_usage] audit start label=%s usage_type=%s user=%s thread=%s "
            "total_tokens=%d cost_micros=%d",
            audit_label,
            usage_type,
            user_id,
            audit_thread_id,
            total_tokens,
            cost_micros,
        )
        async with session_factory() as audit_session:
            try:
                await record_token_usage(
                    audit_session,
                    usage_type=usage_type,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_micros=cost_micros,
                    model_breakdown=model_breakdown,
                    call_details=call_details,
                    thread_id=audit_thread_id,
                    message_id=message_id,
                )
                logger.info(
                    "[llm_usage] audit row staged label=%s usage_type=%s user=%s thread=%s",
                    audit_label,
                    usage_type,
                    user_id,
                    audit_thread_id,
                )
                await audit_session.commit()
                logger.info(
                    "[llm_usage] audit commit OK label=%s usage_type=%s user=%s thread=%s",
                    audit_label,
                    usage_type,
                    user_id,
                    audit_thread_id,
                )
            except BaseException:
                await _rollback_safely(audit_session)
                raise

    try:
        await asyncio.wait_for(_persist(), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning(
            "[llm_usage] audit timed out label=%s usage_type=%s user=%s thread=%s "
            "timeout=%.1fs total_tokens=%d cost_micros=%d",
            audit_label,
            usage_type,
            user_id,
            audit_thread_id,
            timeout_seconds,
            total_tokens,
            cost_micros,
        )
    except Exception:
        logger.exception(
            "[llm_usage] audit failed label=%s usage_type=%s user=%s thread=%s "
            "total_tokens=%d cost_micros=%d",
            audit_label,
            usage_type,
            user_id,
            audit_thread_id,
            total_tokens,
            cost_micros,
        )


@asynccontextmanager
async def recorded_llm_call(
    *,
    user_id: UUID,
    workspace_id: int,
    usage_type: str,
    thread_id: int | None = None,
    message_id: int | None = None,
    call_details: dict[str, Any] | None = None,
    session_factory: UsageSessionFactory | None = None,
    audit_timeout_seconds: float = AUDIT_TIMEOUT_SECONDS,
) -> AsyncIterator[TurnTokenAccumulator]:
    """Record the token usage of one LLM or image call.

    Args:
        user_id: Owner recorded on the ``TokenUsage`` row. For vision during
            indexing this is the *workspace owner*, not the triggering user.
        workspace_id: Recorded on the ``TokenUsage`` row.
        usage_type: ``"image_generation"`` / ``"vision_extraction"`` / etc.
            Recorded on the ``TokenUsage`` row.
        thread_id, message_id: Optional FK columns on ``TokenUsage``.
        call_details: Optional per-call metadata (model name, parameters)
            forwarded to ``record_token_usage``.
        session_factory: Optional async context factory used for the audit
            session. Defaults to ``shielded_async_session`` for route callers;
            Celery callers pass a worker-loop-safe session factory.
        audit_timeout_seconds: Upper bound for TokenUsage audit persistence.
            Audit failure is best-effort.

    Yields:
        The ``TurnTokenAccumulator`` scoped to this call. The caller invokes
        the underlying LLM/image API while inside the ``async with``; the
        ``TokenTrackingCallback`` populates the accumulator automatically.
    """
    session_factory = session_factory or shielded_async_session

    async with scoped_turn() as acc:
        try:
            yield acc
        finally:
            # Always audit, even on exception, so we capture cost when the
            # provider returned successfully but the caller raised later.
            await _record_audit_best_effort(
                session_factory=session_factory,
                usage_type=usage_type,
                workspace_id=workspace_id,
                user_id=user_id,
                prompt_tokens=acc.total_prompt_tokens,
                completion_tokens=acc.total_completion_tokens,
                total_tokens=acc.grand_total,
                cost_micros=acc.total_cost_micros,
                model_breakdown=acc.per_message_summary(),
                call_details=call_details,
                thread_id=thread_id,
                message_id=message_id,
                audit_label=usage_type,
                timeout_seconds=audit_timeout_seconds,
            )


async def resolve_workspace_owner(session: AsyncSession, workspace_id: int) -> UUID:
    """Return the workspace owner, the user a background call's usage belongs to.

    Celery tasks (podcasts, video presentations, queued deliverables) record
    usage against the workspace owner rather than whoever triggered the run.
    """
    result = await session.execute(
        select(Workspace.user_id).where(Workspace.id == workspace_id)
    )
    owner_user_id = result.scalars().first()
    if owner_user_id is None:
        raise ValueError(f"Workspace {workspace_id} not found")
    return owner_user_id


__all__ = ["recorded_llm_call", "resolve_workspace_owner"]
